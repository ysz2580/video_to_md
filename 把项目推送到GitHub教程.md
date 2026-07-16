# 把本地项目推送到 GitHub 教程

> 适用：Windows + PowerShell + git。以你的账号信息为例，命令可直接复制。

## 你的信息（已填好）

| 项 | 值 |
|---|---|
| GitHub 主页 | https://github.com/ysz2580 |
| 提交名字 | `yeyue` |
| 提交邮箱 | `2920207827@qq.com` |

---

## 第 0 步：确认 git 已装好

```powershell
git --version
```

没装就去 https://git-scm.com 下载安装，一路下一步即可。

---

## 第 1 步：在 GitHub 上建一个空仓库

1. 打开 https://github.com/new
2. **Repository name** 填项目名，比如 `my-project`（仓库名建议全小写、单词用 `-` 连）
3. **Public / Private** 按需选
4. **不要勾** "Add a README file"、".gitignore"、"license"——保持空仓库，避免后续合并冲突
5. 点 **Create repository**

建好后页面会给一条类似这样的地址：

```
https://github.com/ysz2580/my-project.git
```

记住它，第 4 步要用。

---

## 第 2 步：本地项目目录初始化 git

进到你的项目文件夹（把下面的路径换成你自己的）：

```powershell
Set-Location "E:\你的项目路径\my-project"
git init -b main
```

`-b main` 表示主分支直接叫 `main`（GitHub 现在默认就是 main）。

---

## 第 3 步：设置提交身份（只需设一次）

如果之前**没**全局配过，给这个仓库设本地身份（只影响当前仓库，不动全局）：

```powershell
git config user.name "yeyue"
git config user.email "2920207827@qq.com"
```

> 想确认设好了：`git config user.name` / `git config user.email`
> 想以后所有项目都用这个身份（全局）：前面加 `--global`，如 `git config --global user.name "yeyue"`

---

## 第 4 步：加 .gitignore（避免把不该上传的东西传上去）

在项目根目录建个 `.gitignore` 文件，按项目类型加内容。通用模板：

```text
# 系统/编辑器杂项
.vscode/
.idea/
*.swp
.DS_Store
Thumbs.db

# 依赖与构建产物（按语言自行增删）
node_modules/
dist/
build/
__pycache__/
*.pyc
.venv/

# 敏感信息（千万别传）
.env
*.key
secrets/
```

> 想偷懒：去 https://github.com/github/gitignore 找对应语言的现成模板复制。

---

## 第 5 步：第一次提交

```powershell
git add .                  # 把所有改动加进暂存区（.gitignore 会自动排除被忽略的）
git commit -m "初始化项目"   # 提交
```

> `git status` 随时能看当前哪些文件改了 / 哪些待提交。

---

## 第 6 步：关联远端仓库并推送

把第 1 步拿到的仓库地址填进来：

```powershell
git remote add origin https://github.com/ysz2580/my-project.git
git push -u origin main
```

- `remote add origin …`：把 GitHub 仓库地址记成 `origin` 这个名字
- `-u origin main`：推送 `main` 分支，并记住「本地 main ↔ 远端 origin/main」的对应关系（以后只需 `git push` 不用再加参数）

**第一次推送可能弹窗让你登录**：
- 如果弹 GitHub 登录窗口 → 用浏览器授权一下即可（GitHub Credential Manager）
- 如果报错 `Authentication failed`：用户名填 GitHub 用户名 `ysz2580`，密码处用 **Personal Access Token**（不是登录密码！）。Token 在 https://github.com/settings/tokens 生成，勾 `repo` 权限。

---

## 第 7 步：以后更新代码（日常三步）

改完代码后：

```powershell
git add .
git commit -m "说明这次改了什么"
git push
```

> 第一次推过 `-u` 后，之后每次只需这三行，不用再写仓库地址。

---

## 常见问题速查

| 现象 | 处理 |
|---|---|
| `Failed to connect to github.com ... 443` | 国内网络波动，多重试几次 push；或挂代理/VPN |
| `Authentication failed` | 密码处要用 Personal Access Token，不是账号密码 |
| `refusing to merge unrelated histories` | 远端仓库建时勾了 README，本地和远端无共同历史。加 `--allow-unrelated-histories` 合并：`git pull origin main --allow-unrelated-histories` 后再 push |
| `src refspec main does not match` | 本地分支可能还叫 `master`。`git branch -M main` 改名后再 push |
| 不想把某文件传上去 | 加进 `.gitignore`；若已被 git 追踪，先 `git rm --cached 文件名` 再提交 |

---

## 完整流程一页纸（对照抄）

```powershell
# 1. 建仓库（GitHub 网页操作），拿到 https://github.com/ysz2580/项目名.git

# 2. 本地初始化
Set-Location "E:\你的项目路径"
git init -b main
git config user.name "yeyue"
git config user.email "2920207827@qq.com"

# 3. .gitignore（手动建文件，或用下面这条快速生成最小版）
@'
.vscode/
.idea/
*.swp
Thumbs.db
node_modules/
dist/
__pycache__/
.env
'@ | Set-Content .gitignore -Encoding utf8

# 4. 提交并推送
git add .
git commit -m "初始化项目"
git remote add origin https://github.com/ysz2580/项目名.git
git push -u origin main
```
