# 全景导览 Agent

把分站拍摄的照片整理成可以环顾、点箭头换站的实景导览。适合校园迎新、场馆导览和固定参观路线。每个点位可使用一张 2:1 球面全景，或一组从同一位置拍摄、相邻画面有重叠的普通照片。

- 在本地工作台处理照片、选择起点并为相邻点位设置往返方向。
- 查看固定路线或场馆点位。画面或方向证据不足时，保留照片预览并标明缺项。
- 保存项目版本，再导出在浏览器中可离线打开的独立 HTML。导出文件会内嵌照片，请先确认照片适合分享。

## 本地运行

本地工作台已在 macOS 的 Python 3.9 上验证。查看球面全景需要支持 WebGL 的浏览器。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

打开 [工作台首页](http://127.0.0.1:18779/) 的「试走示例」体验换站。再打开 [照片与路线编辑器](http://127.0.0.1:18779/?editor=1) 上传自己的照片、连接点位并导出。样例由程序生成。照片和保存版本留在本地。

## 命令行导出

在照片旁创建 `input.json`。每个方向箭头的 `to` 是从 0 开始的目标点位索引，`yaw` 和 `pitch` 为角度，应按自己的场景测定。

```json
{
  "title": "展厅导览",
  "stations": [
    {
      "name": "入口",
      "files": ["entrance.jpg"],
      "spherical": true,
      "links": [{ "to": 1, "yaw": 20, "pitch": 0 }]
    },
    {
      "name": "展厅",
      "files": ["gallery.jpg"],
      "spherical": true,
      "links": [{ "to": 0, "yaw": -20, "pitch": 0 }]
    }
  ]
}
```

```bash
python app.py --manifest input.json --output tour.html
```

把 `tour.html` 发给访客即可。需要保留版本时使用 `--project`。正式导览、固定步行和多楼层模式的流程约定参见 [Agent 技能说明](openclaw-sandbox/agent/skills/space-preview/SKILL.md)。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `app.py`、`project.py` | 工作台接口、导览项目版本及导出 |
| `processing.py`、`engine.py` | 原图处理、拼接与全景视图 |
| `editor.js`、`viewer.js` | 制作界面与离线导览交互 |
| `openclaw-sandbox/` | OpenClaw 技能、接收附件与网关集成源码 |
| `tests/` | 路线、版本和下载相关测试 |

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

OpenClaw 集成需要单独配置平台和沙箱，Dockerfile 默认使用预构建的浏览器基础镜像。上面的本地工作台和离线导出可以独立运行。

项目代码采用 [MIT](LICENSE) 许可。内置的 Pannellum 组件保留其 [原许可证](COPYING)。
