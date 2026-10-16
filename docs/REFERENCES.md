# 接入参考与实现边界

按项目需求，接入前研究了 jxxghp/MoviePilot 的 M-Team 和 qBittorrent 实现，基线为 `v3` 分支提交：

`2149eb2a600539d6170d5ea76a9f6d49e4bd943c`

参考链接：

- [M-Team spider](https://github.com/jxxghp/MoviePilot/blob/2149eb2a600539d6170d5ea76a9f6d49e4bd943c/app/modules/indexer/spider/mtorrent.py)
- [种子换票和下载流程](https://github.com/jxxghp/MoviePilot/blob/2149eb2a600539d6170d5ea76a9f6d49e4bd943c/app/chain/download/submission.py)
- [qBittorrent SDK 封装](https://github.com/jxxghp/MoviePilot/blob/2149eb2a600539d6170d5ea76a9f6d49e4bd943c/app/modules/qbittorrent/qbittorrent.py)
- [下载提交确认](https://github.com/jxxghp/MoviePilot/blob/2149eb2a600539d6170d5ea76a9f6d49e4bd943c/app/modules/qbittorrent/__init__.py)
- [路径规范化](https://github.com/jxxghp/MoviePilot/blob/2149eb2a600539d6170d5ea76a9f6d49e4bd943c/app/modules/__init__.py)
- [qBittorrent 兼容测试](https://github.com/jxxghp/MoviePilot/blob/2149eb2a600539d6170d5ea76a9f6d49e4bd943c/tests/test_qbittorrent_compat.py)
- [qBittorrent 官方 API 文档](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29)
- [qbittorrent-api 文档](https://qbittorrent-api.readthedocs.io/)
- [PT-depiler M-Team 定义](https://github.com/pt-plugins/PT-depiler/blob/master/src/packages/site/definitions/mteam.ts)：核对搜索 `mode=normal`（综合）与 `mode=adult`（成人）的请求参数；不复制实现代码。

## 采用的接口行为

- M-Team 搜索使用 API Key 请求头和 JSON 请求，页码从 1 开始；显式传递 `mode=normal/adult` 区分综合和成人分区。
- 网页地址与 API 地址可分别设置。
- 下载通过资源 ID 换取当前链接，再获取种子文件，而不是拼接普通磁力链接。
- 解析普通优惠、全站促销、单种免费、新旧标签。
- qBittorrent 接入使用同类成熟 SDK，显式指定保存路径和任务自动管理状态。
- 对返回文本或结构化响应不做单一成功假设，按实际 hash 确认任务。
- 处理 v1、v2、hybrid hash，且不改变已有任务的位置或启停状态。

## 没有照搬的部分

- 没有引入 MoviePilot 依赖，也未直接复制其源码。
- 不采用其通用 Base64 下载配方，避免凭据进入前端或通用结果对象；用本项目的专用适配器。
- 不实现媒体识别、订阅、文件整理、全局配置修改、临时标签及复杂目录映射。
- 不默认关闭 TLS 校验，也不为连接下载器而修改未完成文件后缀等全局偏好。
- 使用 qBittorrent 容器路径预设，而不是要求两个容器挂载相同媒体目录。
- M-Team 的 `/torrent/detail`、账号连接测试及免费筛选同样需要真实账号验收；模拟契约测试不是站点官方接口保证。

MoviePilot 使用 GPLv3。本项目当前实现为独立代码；未来若直接复制或改编其源码并分发，需要重新评估对应许可证义务。第三方依赖保留各自许可证。
