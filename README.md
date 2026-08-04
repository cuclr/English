# 每日词库 MVP

一个使用 Flask 和 SQLite 构建的本地背单词网页应用。

## 当前功能

- 创建学习日期
- 输入单词后按需查询本地 PDF 词书
- 提取词书中的释义和重点词组
- 将数据保存在本地 SQLite 数据库
- 按日期显示每日词库

## 本地运行

```powershell
cd D:\Codex\program\English
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

浏览器访问 <http://127.0.0.1:5000>。

数据库会在首次运行时自动创建于 `instance/vocabulary.db`。PDF 只在用户查询时读取，
不会被批量导入数据库；SQLite 只保存用户添加的词条结果。

## MVP 暂不包含

- 编辑与删除
- 登录和多用户
- 复习计划与测试模式
