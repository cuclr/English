# 每日词库 MVP

一个使用 Flask 和 SQLite 构建的本地背单词网页应用。

## 当前功能

- 创建学习日期
- 在指定日期下手动添加单词
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

数据库会在首次运行时自动创建于 `instance/vocabulary.db`。

## MVP 暂不包含

- 单词释义、例句和发音
- 编辑与删除
- 登录和多用户
- 复习计划与测试模式
