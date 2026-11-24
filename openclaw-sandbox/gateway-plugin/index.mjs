import { constants } from 'node:fs';
import { open, readFile, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { homedir } from 'node:os';
import path from 'node:path';
import { guardTool } from './execution-guard.mjs';
import { root } from 'openclaw/plugin-sdk/security-runtime';
import { definePluginEntry } from 'openclaw/plugin-sdk/core';
import { readSessionTranscriptEvents } from 'openclaw/plugin-sdk/session-transcript-runtime';

const ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const photo = /\.(zip|jpe?g|png|webp)$/i;
const maxBytes = 48 * 1024 * 1024;
const ours = session => session?.startsWith('agent:space-preview:');
const valid = ctx => ctx.agentId === 'space-preview' && ours(ctx.sessionKey)
  && ID.test(ctx.sessionId ?? '') && ID.test(ctx.runId ?? '');
const folder = ctx => path.posix.join('media', 'inbound', ctx.sessionId, ctx.runId);
const receipt = ctx => '/workspace/' + folder(ctx) + '/receipt.json';
const pathsOf = (paths, one) => Array.isArray(paths) ? paths : one ? [one] : [];

function matchesType(data, kind) {
  if (kind === 'jpg' || kind === 'jpeg') return data[0] === 255 && data[1] === 216 && data[2] === 255;
  if (kind === 'png') return data.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'));
  if (kind === 'zip') return data.subarray(0, 4).equals(Buffer.from('504b0304', 'hex'));
  return data.toString('ascii', 0, 4) === 'RIFF' && data.toString('ascii', 8, 12) === 'WEBP';
}

export default definePluginEntry({
  id: 'space-preview-intake', name: 'Space Preview Intake',
  register(api) {
    const pending = new Map();
    const keyOf = (session, run) => session + ':' + run;
    api.on('message_received', (event, ctx) => {
      const session = event.sessionKey ?? ctx.sessionKey;
      const run = event.messageId ?? ctx.messageId;
      const paths = pathsOf(event.metadata?.mediaPaths, event.metadata?.mediaPath);
      if (!ours(session) || !ID.test(run ?? '') || !paths.length) return;
      const key = keyOf(session, run);
      const timer = setTimeout(() => pending.delete(key), 300000);
      timer.unref();
      pending.set(key, paths);
    });

    async function stage(ctx) {
      const started = performance.now();
      const relative = folder(ctx);
      const directory = api.pluginConfig.directory;
      try {
        return JSON.parse(await readFile(path.join(directory, relative, 'receipt.json'), 'utf8'));
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
      const key = keyOf(ctx.sessionKey, ctx.runId);
      const workspace = await root(directory);
      // Vision webchat omits offloaded JPGs from dispatch metadata. The runtime
      // records the trusted original paths on the exact user turn before exec.
      const events = await readSessionTranscriptEvents({agentId: ctx.agentId, sessionKey: ctx.sessionKey, sessionId: ctx.sessionId});
      const user = events.findLast(e => e.type === 'message' && e.message?.role === 'user' &&
        e.message.idempotencyKey === ctx.runId + ':user')?.message;
      const inbound = await realpath(path.join(process.env.OPENCLAW_STATE_DIR || path.join(homedir(), '.openclaw'), 'media', 'inbound'));
      const paths = [...new Set([...pathsOf(user?.MediaPaths, user?.MediaPath), ...(pending.get(key) || [])]
        .filter(source => typeof source === 'string')
        .map(source => source.startsWith('media://inbound/') ? path.join(inbound, source.slice(16)) : source))];
      const files = [];
      let total = 0;
      for (const [index, source] of paths.entries()) {
        const storedName = path.basename(source);
        const kind = storedName.match(photo)?.[1]?.toLowerCase();
        const extension = path.extname(storedName);
        const stem = path.basename(storedName, extension);
        const name = storedName.replace(/---[0-9a-f-]{36}(?=\.)/i, '');
        const display_name = ID.test(stem) ? `附件 ${index + 1}` : name;
        const file = {name, display_name, status: 'missing'};
        files.push(file);
        if (!kind || path.dirname(source) !== inbound) {
          file.reason = '不支持的附件类型或原件位置';
          continue;
        }
        try {
          const fd = await open(source, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
          let data;
          try {
            const st = await fd.stat();
            if (!st.isFile() || st.size <= 0 || st.size > maxBytes - total) throw new Error('size');
            data = await fd.readFile();
            if (data.length !== st.size || !matchesType(data, kind)) throw new Error('format');
          } finally { await fd.close(); }
          const destination = relative + '/' + (index + 1) + '.' + kind;
          await workspace.write(destination, data, {mkdir: true, mode: 0o644});
          total += data.length;
          Object.assign(file, {status: 'received', path: '/workspace/' + destination, bytes: data.length,
            sha256: createHash('sha256').update(data).digest('hex')});
        } catch {
          file.reason = '原件无法读取、格式不符或超过收件限制';
        }
      }
      const result = {session: ctx.sessionKey, session_id: ctx.sessionId, run: ctx.runId, files,
        staging_ms: Math.round(performance.now() - started)};
      await workspace.write(relative + '/receipt.json', JSON.stringify(result), {mkdir: true, mode: 0o644});
      api.logger.info(`space-preview receipt run ${ctx.runId}: ${files.filter(file => file.status === 'received').length}/${files.length}`);
      return result;
    }

    api.on('before_prompt_build', (_event, ctx) => {
      if (!valid(ctx)) return;
      return {prependContext: '当前 OpenClaw 消息的唯一原件回执路径：' + receipt(ctx) +
        '。本轮有上传、图片块或用户表示刚发送附件时，先执行 python3 /workspace/prepare_photos.py --receipt ' +
        receipt(ctx) + ' /workspace/tasks/' + ctx.sessionId + '/uploads/' + ctx.runId +
        '。不要读其他消息或任务的同名图。'};
    });

    api.on('before_tool_call', async (event, ctx) => {
      if (ctx.agentId !== 'space-preview' || !ours(ctx.sessionKey)) return;
      const blocked = guardTool(event);
      if (blocked) return {block: true, blockReason: blocked};
      const command = event.params.command;
      if (!valid(ctx) || event.toolName !== 'exec' || typeof command !== 'string' ||
          !command.startsWith('python3 /workspace/prepare_photos.py --receipt ' + receipt(ctx) + ' ')) return;
      try { await stage(ctx); }
      catch (error) {
        api.logger.warn(`space-preview receipt failed, run ${ctx.runId}: ${error.code || error.name}`);
        return {block: true, blockReason: '本轮原件未进入任务。保留原任务和草稿，请只重发本轮缺失的 JPG，或将该张单独打包成 ZIP。'};
      }
    });
  }
});
