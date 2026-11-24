import path from 'node:path';

const scripts = new Set(['app.py', 'prepare_photos.py', 'inspect_spheres.py', 'unpack_photos.py']);
const reason = '制作任务只能写 tasks 内的清单或 memory/YYYY-MM-DD.md 日记，并直接调用 /workspace 下的既有业务脚本；不能执行临时脚本、Shell 组合命令或修改共享应用。请用 write 写清单，或用 app.py 的 --opening 续改。';
const within = (file, directory) => typeof file === 'string' && file.startsWith(directory + '/') &&
  path.posix.normalize(file) === file && !file.endsWith('/');

// No argparse abbreviation, standalone legacy export, or caller-chosen shared output.
function options(args, schema) {
  const values = {}, positional = [];
  for (let i = 0; i < args.length; i++) {
    const key = args[i];
    if (!key.startsWith('--')) { positional.push(key); continue; }
    if (!Object.hasOwn(schema, key) || Object.hasOwn(values, key)) return null;
    const count = schema[key], items = [];
    while (i + 1 < args.length && !args[i + 1].startsWith('--') && (count < 0 || items.length < count)) {
      items.push(args[++i]);
    }
    if (count >= 0 && items.length !== count) return null;
    values[key] = items;
  }
  return {values, positional};
}

function businessArgs(script, args) {
  if (args.length === 1 && args[0] === '--help') return true;
  if (script === 'app.py') {
    const parsed = options(args, {'--project':1,'--parent':1,'--manifest':1,'--port':1,
      '--opening':2,'--opening-photo':1,'--opening-at':1,'--venue-level':1,'--venue-recommended':-1,
      '--intake':1,'--bind-spheres':0,'--resume':0,'--chat-download':0,'--preview-download':0,
      '--chat-files':0,'--chat-sample':0,'--require-tour':0,'--require-walk':0,'--require-venue':0,
      '--require-panorama':0});
    if (!parsed || parsed.positional.length) return false;
    const v = parsed.values;
    if (v['--chat-sample']) return Object.keys(v).every(k => ['--chat-sample','--port'].includes(k));
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(v['--project']?.[0] || '')) return false;
    if (!['--manifest','--resume','--opening','--opening-photo','--venue-level','--venue-recommended'].some(k => k in v)) return false;
    if (v['--manifest'] && !within(v['--manifest'][0], '/workspace/tasks')) return false;
    if (v['--intake'] && !within(v['--intake'][0], '/workspace/media/inbound')) return false;
    return true;
  }
  if (script === 'prepare_photos.py') {
    const parsed = options(args, {'--receipt':1,'--first-look':1,'--title':1,'--purpose':1});
    if (!parsed || !within(parsed.positional[0], '/workspace/tasks')) return false;
    return parsed.positional.slice(1).every(p => within(p, '/workspace')) &&
      (!parsed.values['--receipt'] || within(parsed.values['--receipt'][0], '/workspace/media/inbound'));
  }
  if (script === 'inspect_spheres.py') {
    const parsed = options(args, {'--view':2});
    if (!parsed || parsed.positional.length !== 2) return false;
    const [file, output] = parsed.positional;
    return within(file, '/workspace') && within(output, '/workspace/tasks');
  }
  return args.length === 2 && within(args[0], '/workspace') && within(args[1], '/workspace/tasks');
}

// Deliberately accept a small literal command language, not arbitrary shell.
function words(command) {
  if (typeof command !== 'string' || /[\r\n\\$`;&|<>]/.test(command)) return null;
  const tokens = command.match(/"[^"\r\n]*"|'[^'\r\n]*'|[^\s"']+/g) || [];
  if (tokens.join(' ').replace(/\s+/g, ' ') !== command.trim().replace(/\s+/g, ' ')) return null;
  return tokens.map(word => /^['"]/.test(word) ? word.slice(1, -1) : word);
}

export function guardTool(event) {
  const params = event.params || {};
  if (event.toolName === 'write') {
    const file = params.path ?? params.file_path;
    // OpenClaw exposes append-only daily memory during pre-compaction flushes.
    const dailyMemory = typeof file === 'string' && /^(?:\/workspace\/)?memory\/\d{4}-\d{2}-\d{2}\.md$/.test(file);
    if (!within(file, '/workspace/tasks') && !dailyMemory) return reason;
  } else if (event.toolName === 'apply_patch') return reason;
  else if (event.toolName === 'exec') {
    const args = words(params.command);
    if (!args || args.length < 2 || args[0] !== 'python3' ||
        !scripts.has(args[1].replace('/workspace/', '')) ||
        args[1] !== '/workspace/' + path.posix.basename(args[1]) ||
        !businessArgs(path.posix.basename(args[1]), args.slice(2))) return reason;
  }
  return null;
}
