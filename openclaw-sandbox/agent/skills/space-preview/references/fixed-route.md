# 固定步行路线：从指定地铁出口到指定入口

地点可选时，先从实际素材核可辨起终点、视线遮挡或会影响到达的转向，再决定路线；用户指定地点时按实际覆盖交付。普通照片与全景都遵循[画面、原图和门位复核](review.md)。

用户明确要求用有日期、许可的历史完整球面作虚拟参观、并未要求今天现场带路时，仍完成固定路线的去回通路；`fixed_route` 可写 `visit_mode:"virtual",access:"historical",captured_on:"YYYY-MM-DD",public_photos:true`。拍摄日期取真实来源，逐站保留完整球面与 `photo_sources`，逐方向填写 `walk` 依据和画面 `links`，来源和实际通路要求不减。`checked_on` 是本次资料核对日期；不写当前适用日期或虚构 `access:"open"`。访客页会明确显示历史虚拟参观及不代表今天开放。此模式仍用 `--require-walk --require-panorama --chat-download`；缺段的 `archive_notice` 草稿不能靠改模式变成成品。

1. 仍在同一对话收图。用 `prepare_photos.py` 一次准备真实附件，逐张读取联系表；只用确实看见的标牌、用户的现场踏勘和连续拍摄关系拟站序。同一站择最清楚的看标牌主图，剩余合用图可放 `alternates`；原照优先，可靠球面才写 `spherical:true`。没看到实拍或不知道相邻通路时先保留草稿，不按上传顺序、地图距离或地名补路。以 3–5 个实际决策点为目标，含指定出口和抵达点。只有官网信息时只核对适用人群、出入口和时间，不写中途走法。
2. 清单写 `visitor_route:true`，入口为第一站。新增：
   - 顶层 `fixed_route`：`start,destination,audience,coverage,checked_on,first_action,access,verify_note,verify_url`。`first_action` 只写有实据的起步动作（例如先认准某标牌）；`checked_on` 用现场核对日期 YYYY-MM-DD。无证据则留空。`access` 为 `open`、`closed`、`wrong_start`、`ticket_unknown` 或 `unknown`；有日期限制时写 `valid_from,valid_until`，有时段或人群限制在 `coverage` 明示。核实入口用有依据的 HTTPS 官网。过街、进安检区、无障碍等只在现场证据充分时写；过期、关闭或票区不符时给核实入口，不自编绕行。
   - `fixed_route.survey` 保存 `date,source,permission,continuity`，对应实际踏勘记录和来源授权。按站在 `survey_note` 保留照片、时间、观察位置、朝向、开放情况及前后点依据；不得把官网、上传时间、猜测或历史活动当成现场记录。来源授权和署名如须随成品发出，再写顶层 `attribution`。
   - 用户明确要求使用公开照片时，可写 `fixed_route.public_photos:true`。`checked_on` 记公开资料核对日期，不填 `survey`。只保留实际有用途的 2–5 点；每站的 `photo_sources` 按 `files`、`alternates` 的顺序逐张写 `author,license,url,license_url,caption`，标明照片年份、画面用途及裁切等修改。没有可核实转载许可的照片不嵌入。建筑远景只用来辨认建筑，若照片看不到门牌，终点提示读实际门牌。普通公开照片路线只按馆方或交通管理方明确发布的相邻段走法填写 `walk`，不放画面箭头。要求实景全景时，同一次公开连续实拍中清晰可见的相邻人行道、转向、地标也可作为逐方向依据。相机拍在车道上时，只引向画面中可辨的人行空间，不让访客沿车道走。每个方向另写实际照片或官方记录的 HTTPS `evidence_url`。依据不足先保存草稿，只问阻断的一段。标明公开资料核对和未现场踏勘。
   - 已确认、无结束日期且没有全景的单向公开照片路线会导出为无脚本的逐步图文页。下一步文字可以直接打开，照片显示不阻塞导航。标牌确实入镜且在窄屏太小时，可在父版清单给该站加 `sign_crop:{asset,box}`。填该站已经保留的 `originals/…` 路径和校正 EXIF 后原照的 `[左,上,右,下]` 相对坐标（0 到 1）。只裁看得见的标牌，成品标为原照裁切并保留完整照片。看不见门牌时，写清要核对的入口及地址，把建筑照放在参照照片里。
   - 各站有稳定 `id,name,landmark,files`。原照首屏可写 `opening_photo`（从 0 起，先 `files` 后 `alternates`）。方向在源站 `walk`，键为相邻站 ID。例如 `"walk":{"east":{"see":"实拍可见的固定标牌","action":"有依据的走法","arrive":"下一站可见地标","confirmed_by":"现场记录的提供者","evidence":"本方向实际依据及照片或记录编号"}}`。去、回分别在各自源站记录，不沿用去程证据推断回程。不确定留 `pending`（具体缺口）；不写 `confirmed_by/evidence`。如果已有一个明确且可现场确认的候选，暂填 `see/action/arrive`，草稿不会把候选动作当走法；候选文字会在工作台点选时显示。连候选走法都不清楚则给站点任务链接，问一项描述或补拍，不让用户对着未知路线点选。可选实景箭头沿用 `links` 的画面版本绑定规则；没有通路依据时不能用 `cut` 代替。
   - 确认的公开照片走法若有官方文字和图示支持，可加 `orientation:{road,heading,distance,left}`，用于按行走方向画简单示意，四项都须有依据。若缺左手地标或其他一项，省略该字段。不要把示意当作地图比例、过街点或入口实拍。两站单向、首站有标牌局部和该示意时，同版 JPG 提供无需打开 HTML 的简明走法，终点只写可核对的入口提示。
3. 用 `python3 /workspace/app.py --project <任务> --manifest <清单> --port 18792` 先保存，首条有效输出用回执的 `review_url`、`route_card_url` 和 `recover_url`。写明起终点、实际能试到哪一站，只提第一处阻断去程的问题；有实拍候选且用户需要点选时直达 `confirm_url`。待定方向不交付成品下载；缺回程但去程都已确认时明确「单向路线指引」。
4. 点选保存后，从同一项目执行 `--resume` 读取最新保存版，核对本任务与父版。用户用文字给足本方向证据时，只在父版清单改对应源站的方向，用该版本回执的 `walk_frames[源站][目标站]` 值作为方向 `frame`，保留其它字段，再带确切 `--parent` 保存。只换首屏照片时用 `--project <任务> --parent <保存版> --opening-photo <编号> --port 18792`；球面仍用 `--opening YAW PITCH`。想改途经站可加 `--opening-at <站点>`。切勿改站序或重写其它方向证据。
5. 用 `--project <任务> --resume --parent <确切保存版> --require-walk --chat-download --port 18792` 同版取件，聊天先给 `review_url`「打开全景预览」和 `download`「下载离线导览」，再给同版 `route_card_url`。给访客的说明写适用入口、核对日期、覆盖和已确认方向。文件超聊天渠道限制时，制作方在当前电脑点本机下载，转发落盘后的 HTML 或路线图；回环地址只供当前电脑取件，勿称手机收件已通过或对外代发。
6. 工具失败按动作区分：附件不存在先核本轮收件；父版过时打开同任务最新保存版，确认变化再继续；未达门槛先给草稿及具体缺口；取件服务未响应则说明本机服务或恢复入口，不重收图。模型断流且不知道是否已保存时，先用 `recover_url` 或 `--resume` 取回同任务已有版本。保存已存在时只取件，不重复处理。
要求固定起终点的实景全景时，在清单写 `fixed_route.require_panorama:true`，发布与同版恢复均在 `--require-walk` 后加 `--require-panorama`。完整球面路线可保留至 32 站；上文 2–5／3–5 的决策点数量仅适用于普通照片路线，不得因此丢掉必要过渡机位，也不能把不同物理点合为一站。三个以上决策点须有可拖实景，逐方向同时填写 `walk` 的走法依据和源站 `links` 的全景箭头。公开球面逐张保留署名、许可和原图来源，普通公开照片仍可按本参考保存为照片路线。


已有多站普通全景首次补成固定路线时，先对确切父版执行 --resume，读取实际回执的 walk_frames；普通导览也提供相邻站的原图绑定，它不是通路确认。先保持普通模式，通过 amend 分批补逐图 photo_sources 和各方向 walk（frame、see/action/arrive、confirmed_by/evidence/evidence_url）；同站 walk 整体替换，每次使用最新确切父版。所有方向证据齐后，最后一次 amend 加 fixed_route 并移除冲突的顶层 destination、纯历史 archive_notice，再完整严格取件。不要先加 fixed_route，因为无 walk 会过滤既有箭头而触发保护；不重抄全部原图或箭头，也不降低保护。
