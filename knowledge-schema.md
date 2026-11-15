# PhotonGuard 知识条目格式

本文对应 `0.2.0rc28` 的实际知识包与校验入口。知识包可独立扩展，工作台继续使用原有检索、阅读和问答入口。

## 包结构

|字段|作用|
|---|---|
|`id`、`version`|知识包标识和版本；同一版本内容变化时提高版本|
|`sources`|以来源 ID 为键的来源记录|
|`entries`|条目正文、条件、公式及关联|
|`topics`|可选的专题与学习组织|

## 条目

实际示例：`app/source/photonguard/experiments/knowledge_data/pack-optics-core.json`。

|字段|内容|
|---|---|
|`id`、`title`、`terms`|唯一编号、标题和别名|
|`definition`、`body`、`example`|定义、中文正文与例子|
|`conditions`、`caution`|适用条件与需要区分的概念|
|`formula`、`variables`|可选公式及变量；没有公式时可为 `null`|
|`sources`|引用本包来源 ID|
|`claims`|部分条目的关键结论、关联来源及类别；并非所有条目都有此字段|
|`prerequisites`、`related`|前置与相关条目|
|`license`、`origin`、`status`、`review`|条目许可、来源类型、发布状态和维护记录|

## 来源

来源包含 `title`、`author`、`edition`、`url`、`section`、`accessed`、`license`、`rights`、`fingerprint` 与 `excluded_material` 等实际字段。`REFERENCE_ONLY` 记录按引用使用的权利状态，`fingerprint` 记录所核对资料的文件指纹；`excluded_material` 说明没有随包分发的内容。

知识条目以结构化字段记录来源、权利状态、排除内容及关键结论，便于审查与维护。字段记录与正文、引用和具体使用方式需要共同核查。

团队组织知识结构，借助 AI 整理中文讲解，并通过来源定位、公式检查和重点抽查维护内容。格式校验检查结构及关联，不代表完成全量逐篇人工审核。

## 校验与安装

使用工作台环境的 Python，从 `app/source` 执行：

```sh
python -m photonguard.experiments.knowledge_pack --validate /path/to/pack.json
python -m photonguard.experiments.knowledge_pack --workspace ../data/workspace --install /path/to/pack.json
python -m photonguard.experiments.knowledge_pack --workspace ../data/workspace --remove example-pack
```

可从 `docs/示例专题包.json` 开始。新增内容需保留可核对的来源和适用条件；安装失败时继续使用已有知识包。数值模型扩展从 `app/source/photonguard/experiments/` 的适配层与对应测试开始。
