# InsightPulse

[在线体验](https://feyzha-insightpulse.vercel.app) · [GitHub](https://github.com/FeyZha/insightpulse)

将 Steam 用户评论整理成可追溯的问题、变化对照与行动建议。

## 功能

- 反馈总览：问题分布、类别、表现及原话依据。
- 变化追踪：比较已保存的评论样本，追溯两侧证据。
- 决策建议：基于证据摘要与规则给出调查、验证、行动或观察建议。
- 人工评审：采纳、修改、驳回，以及执行和验证记录。

## 在线演示

Demo 内置 Dinner Date 与 Ostranauts 的已有分析结果。浏览、筛选和证据查看不需要模型；评审操作仅保存在当前浏览器，可通过清除站点数据重置。没有大模型 API 设置入口。

## 快速开始

```sh
cd frontend
npm ci
npm run dev
```

## 构建与检查

```sh
cd frontend
npm run build
npm run check
```

根目录 `vercel.json` 可直接用于部署。运行 Demo 无需后端、数据库或模型密钥。

## 结构

- `frontend/src/`：React 页面、图表和交互。
- `frontend/public/demo-data.json` 与 `fixtures/`：按需读取的分析结果与评论证据快照。
- `frontend/src/lib/demo-api.ts`：读取预制数据及浏览器内评审存储。
- 根目录 Python 模块：本地分析、证据摘要、决策规则与追踪实现。

## 数据与限制

演示中的数字来自已保存测试样本，不代表生产用户效果。样本占比变化不能直接解释为产品改动带来的因果效果。预制建议不随当前点击重新生成；本地评审记录不代表真实团队采纳或业务改善。Steam 评论与游戏素材权利属于原作者，源码尚未授予额外开源许可。
