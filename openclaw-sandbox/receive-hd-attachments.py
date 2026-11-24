"""Preserve HD space-preview attachments on the supported OpenClaw 2026.6.11 build.

The authenticated WS envelope holds one <=48 MiB decoded batch. Only space-preview
accepts photos up to 32 MiB; large photos are offloaded, not inlined into the model.
Other agents keep their existing decoded attachment / image limits.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise SystemExit('Unsupported or already patched bundle; no changes written.')
    return source.replace(before, after, 1)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('dist', type=Path)
parser.add_argument('backup', type=Path)
parser.add_argument('--node', default='node')
args = parser.parse_args()
if args.backup.exists():
    raise SystemExit('Backup already exists; no changes written.')
constants = args.dist / 'server-constants-BGwLM6XN.js'
chat = args.dist / 'chat-DFeIryVW.js'
normalizer = args.dist / 'attachment-normalize-DrluG6l3.js'
updates = {
    constants: replace_once(constants.read_text(),
        'const MAX_PAYLOAD_BYTES = 25 * 1024 * 1024;',
        'const MAX_PAYLOAD_BYTES = 68 * 1024 * 1024;'),
    chat: replace_once(chat.read_text(),
        'maxBytes: resolveChatAttachmentMaxBytes(cfg),',
        'maxBytes: agentId === "space-preview" ? 48 * 1024 * 1024 : resolveChatAttachmentMaxBytes(cfg),\n'
        '\t\t\t\t\t\tmaxImageBytes: agentId === "space-preview" ? 32 * 1024 * 1024 : void 0,'),
    normalizer: replace_once(normalizer.read_text(),
        'if (isImage && sizeBytes > 6291456) throw new Error(`attachment ${label}: image exceeds size limit (${sizeBytes} > ${MAX_IMAGE_BYTES} bytes)`);',
        'const maxImageBytes = opts?.maxImageBytes ?? MAX_IMAGE_BYTES;\n'
        '\t\t\tif (isImage && sizeBytes > maxImageBytes) throw new Error(`attachment ${label}: image exceeds size limit (${sizeBytes} > ${maxImageBytes} bytes)`);'),
}
with tempfile.TemporaryDirectory() as temporary:
    for bundle, content in updates.items():
        staged = Path(temporary) / bundle.with_suffix('.mjs').name
        staged.write_text(content)
        subprocess.run([args.node, '--check', staged], check=True)
args.backup.mkdir(parents=True)
for bundle in updates:
    shutil.copy2(bundle, args.backup / bundle.name)
for bundle, content in updates.items():
    with tempfile.NamedTemporaryFile(mode='w', dir=bundle.parent, delete=False) as staged:
        staged.write(content)
    target = Path(staged.name)
    target.chmod(bundle.stat().st_mode)
    target.replace(bundle)
print(args.backup)
