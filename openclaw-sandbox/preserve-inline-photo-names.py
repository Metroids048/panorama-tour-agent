"""Keep the WebChat filename through inline image persistence on OpenClaw 2026.6.11."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise SystemExit("OpenClaw bundle did not match the supported build; no changes written.")
    return source.replace(before, after, 1)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("dist", type=Path)
parser.add_argument("backup", type=Path)
parser.add_argument("--node", default="node")
args = parser.parse_args()
parser_file = args.dist / "attachment-normalize-DrluG6l3.js"
chat_file = args.dist / "chat-DFeIryVW.js"
normalizer = replace_once(
    parser_file.read_text(),
    'images.push({\n\t\t\t\t\ttype: "image",\n\t\t\t\t\tdata: b64,\n\t\t\t\t\tmimeType: finalMime\n\t\t\t\t});',
    'const image = { type: "image", data: b64, mimeType: finalMime };\n'
    '\t\t\t\tif (opts?.preserveInlineFileNames && att.fileName) '
    'Object.defineProperty(image, "originalFilename", { value: ensureExtension(label, finalMime) });\n'
    '\t\t\t\timages.push(image);',
)
chat = replace_once(
    chat_file.read_text(),
    'img.mimeType, "inbound"));',
    'img.mimeType, "inbound", void 0, img.originalFilename));',
)
chat = replace_once(
    chat,
    'supportsImages,\n\t\t\t\t\t\tacceptNonImage: true',
    'supportsImages,\n\t\t\t\t\t\tpreserveInlineFileNames: agentId === "space-preview",\n'
    '\t\t\t\t\t\tacceptNonImage: true',
)

with tempfile.TemporaryDirectory() as temp:
    for bundle, source in ((parser_file, normalizer), (chat_file, chat)):
        check = Path(temp) / bundle.with_suffix(".mjs").name
        check.write_text(source)
        subprocess.run([args.node, "--check", check], check=True)
args.backup.mkdir(parents=True)
for bundle in (parser_file, chat_file):
    shutil.copy2(bundle, args.backup / bundle.name)
for bundle, source in ((parser_file, normalizer), (chat_file, chat)):
    with tempfile.NamedTemporaryFile(mode="w", dir=bundle.parent, delete=False) as staged:
        staged.write(source)
    target = Path(staged.name)
    target.chmod(bundle.stat().st_mode)
    target.replace(bundle)
print(args.backup)
