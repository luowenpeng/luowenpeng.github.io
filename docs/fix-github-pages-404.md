# 从 404 到 200：一次 GitHub Pages 网站修复实战

> **TL;DR**：我的 Docsify 个人博客网站 `luowenpeng.com` 一直返回 404，经过系统诊断发现 4 个严重问题——Jekyll 配置冲突导致构建失败、核心 CSS/JS 文件缺失、拼写错误让功能失效、封面页配置重复。逐项修复后网站恢复，整个过程踩了不少坑，记录下来供参考。

---

## 一、问题背景

我有一个基于 [Docsify](https://docsify.js.org/) 的个人博客网站，托管在 GitHub Pages 上，绑定了自定义域名 `luowenpeng.com`。某天发现网站打不开了，浏览器直接显示 GitHub Pages 的 404 页面。

仓库地址：[luowenpeng/luowenpeng.github.io](https://github.com/luowenpeng/luowenpeng.github.io)

---

## 二、诊断过程

### 2.1 第一印象：DNS 问题？

网站 404，第一反应是 DNS 配置出问题了。用 `nslookup` 检查域名解析：

```bash
nslookup luowenpeng.com 8.8.8.8
```

结果：4 条 A 记录全部正确指向 GitHub Pages 的 IP（`185.199.108~111.153`）。

**结论：DNS 没问题。**

### 2.2 第二层：HTTPS 重定向

用 `curl` 检查 HTTP 响应头：

```bash
curl -sI https://luowenpeng.github.io
```

返回 `301` 重定向到 `http://luowenpeng.com/`（注意是 HTTP），而 `luowenpeng.com` 本身返回 `404`。

**结论：重定向链路存在，但目标页面不存在——问题在 GitHub Pages 构建端。**

### 2.3 第三层：代码审查

逐个审查仓库核心文件，发现了真正的问题：

| 文件 | 问题 |
|------|------|
| `_config.yml` | 内容是 `theme: jekyll-theme-cayman`——Jekyll 主题配置 |
| `index.html` | CSS/JS 引用本地路径，但 `css/` 和 `scripts/` 目录不存在 |
| `index.html` | `loadNavbar: ture` 和 `coverpage: ture`——拼写错误 |
| `index.html` | `coverpage` 属性赋值了两次 |

---

## 三、4 个严重问题详解与修复

### 🔴 问题 1：`_config.yml` 导致 Jekyll 构建冲突

**症状**：网站 404

**根因**：仓库里有一个 `_config.yml`，内容是：

```yaml
theme: jekyll-theme-cayman
```

这是 Jekyll 的主题配置。但我的网站用的是 Docsify——纯前端 SPA 方案，根本不需要 Jekyll。GitHub Pages 检测到这个文件后，尝试用 Jekyll 构建站点，但 `jekyll-theme-cayman` 这个主题根本没安装，构建失败，于是返回 404。

**修复**：直接删除 `_config.yml`。

> **经验教训**：Docsify 项目里不应该有 `_config.yml`。虽然根目录有 `.nojekyll` 文件，但 `_config.yml` 的存在仍然可能干扰 GitHub Pages 的构建判断。删掉它是最干净的做法。

---

### 🔴 问题 2：CSS/JS 核心资源文件缺失

**症状**：即使网站能访问，页面也无法渲染（白屏或只有文字）

**根因**：`index.html` 引用了本地文件路径：

```html
<link rel="stylesheet" href="/css/vue.css">
<script src="/scripts/docsify.min.js"></script>
<script src="/scripts/search.min.js"></script>
<script src="/scripts/ga.min.js"></script>
```

但仓库里**根本没有** `css/` 和 `scripts/` 目录。这些文件从未上传过。

**修复**：改用 CDN 引用：

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/docsify@4/lib/themes/vue.css">
<script src="https://cdn.jsdelivr.net/npm/docsify@4/lib/docsify.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/docsify@4/lib/plugins/search.min.js"></script>
```

> **经验教训**：Docsify 官方推荐使用 CDN。本地文件方式需要手动维护版本更新，且容易遗忘上传。CDN 方案简单稳定，一行搞定。
>
> 另外，`ga.min.js`（Google Analytics）被引用了但 `$docsify` 配置里没有 `ga` 跟踪 ID，属于无效引用，一并删除。

---

### 🔴 问题 3：`ture` 拼写错误

**症状**：导航栏不加载、封面页不显示

**根因**：`index.html` 中 JavaScript 配置：

```javascript
loadNavbar: ture,    // ❌ 应为 true
coverpage: ture,     // ❌ 应为 true
```

`ture` 不是 JavaScript 的有效关键字，会被当作未定义变量，值为 `undefined`，等价于 `false`。

**修复**：

```javascript
loadNavbar: true,
coverpage: true,
```

> **经验教训**：拼写错误是最常见也最容易被忽视的 Bug。建议在编辑配置后用浏览器控制台检查是否有报错，或使用 ESLint 等工具做语法检查。

---

### 🔴 问题 4：coverpage 重复配置

**症状**：封面页不显示

**根因**：

```javascript
coverpage: ture,        // 第一个赋值（无效拼写）
coverpage: 'cover.md', // 第二个赋值，覆盖了上一个
```

双重问题：
1. 重复赋值——JavaScript 中后者覆盖前者，所以实际生效的是 `coverpage: 'cover.md'`
2. 文件名不匹配——配置指向 `cover.md`，但仓库里的文件叫 `_coverpage.md`

**修复**：保留一行即可：

```javascript
coverpage: true,
```

Docsify 默认读取 `_coverpage.md`，无需指定文件名。

> **经验教训**：Docsify 的 `coverpage` 配置，`true` 就够了，它会自动找 `_coverpage.md`。不需要手动指定文件名，除非你的封面文件叫别的名字。

---

## 四、修复后的 index.html

修复完成后的完整 `index.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>雒文鹏的博客</title>
  <meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1">
  <meta name="description" content="格物致知 · 诚意正心 · 修身齐家">
  <meta name="viewport" content="width=device-width, user-scalable=no, initial-scale=1.0, maximum-scale=1.0, minimum-scale=1.0">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/docsify@4/lib/themes/vue.css">
  <style>
    img {text-align: center;}
  </style>
</head>
<body>
  <div id="app">网页加载中...请稍等</div>
  <script>
    window.$docsify = {
      el: '#app',
      repo: 'https://github.com/luowenpeng/luowenpeng.github.io/',
      loadNavbar: true,
      name: 'HOME',
      nameLink: '/',
      mergeNavbar: false,
      maxLevel: 2,
      subMaxLevel: 3,
      coverpage: true,
      search: 'auto',
    }
  </script>
  <script src="https://cdn.jsdelivr.net/npm/docsify@4/lib/docsify.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/docsify@4/lib/plugins/search.min.js"></script>
</body>
</html>
```

对比原版，改动点一目了然：

| 改动项 | 修复前 | 修复后 |
|--------|--------|--------|
| `lang` | `en` | `zh-CN` |
| `title` | `luowp` | `雒文鹏的博客` |
| `meta description` | `Description` | `格物致知 · 诚意正心 · 修身齐家` |
| CSS 引用 | `/css/vue.css`（不存在） | CDN |
| JS 引用 | `/scripts/*.js`（不存在） | CDN |
| `loadNavbar` | `ture` | `true` |
| `coverpage` | `ture` + `'cover.md'`（重复+错名） | `true` |
| `ga.min.js` | 引用但无配置 | 删除 |
| `<nav>` 标签 | 手写导航（与 Docsify 冲突） | 删除 |
| `_config.yml` | 存在（Jekyll 配置） | 删除 |

---

## 五、推送踩坑：HTTPS 不通，SSH 救场

代码改完了，推送又踩了一个坑。

### 5.1 HTTPS 推送失败

```bash
git push origin master
# fatal: unable to access '...': OpenSSL SSL_read: Connection was reset, errno 10054
```

这是国内访问 GitHub 的经典问题——网络连接被重置。尝试了增大缓冲区等方法都不行：

```bash
git config --global http.postBuffer 524288000  # 没用
```

### 5.2 GitHub API 也没写权限

尝试通过 GitHub MCP 连接器的 `push_files` API 推送，结果：

```
403 Resource not accessible by integration
```

连接器只有读权限，写操作被拒绝。

### 5.3 最终方案：SSH 推送

配置 SSH 密钥后推送成功，步骤如下：

**1. 生成 SSH 密钥：**

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
# 一路回车，使用默认路径
```

**2. 添加公钥到 GitHub：**

```bash
type %USERPROFILE%\.ssh\id_ed25519.pub
# 复制输出内容 → GitHub Settings → SSH Keys → New SSH Key
```

**3. 切换远程地址并推送：**

```bash
git remote set-url origin git@github.com:luowenpeng/luowenpeng.github.io.git
git push origin master
```

> **经验教训**：在国内环境下，配置 SSH 密钥是推送到 GitHub 最稳定的方式。HTTPS 方式经常因网络问题中断，而 SSH 协议更稳定。建议一开始就配好 SSH，省得后面折腾。

---

## 六、验证结果

推送后 1 分钟内，网站恢复：

```bash
curl -sI https://luowenpeng.com
# HTTP/1.1 200 OK ✅
```

浏览器访问 `https://luowenpeng.com`，Docsify 正常渲染，封面页、导航栏、侧边栏、搜索功能全部生效。

---

## 七、总结：5 条经验

### 1. Docsify 项目不要留 Jekyll 配置

`_config.yml` 是 Jekyll 的配置文件，Docsify 项目完全不需要它。它的存在会让 GitHub Pages 误判构建方式，导致 404。删掉它，保留 `.nojekyll` 即可。

### 2. 核心资源优先用 CDN

Docsify 的 CSS 和 JS 文件通过 CDN 引用最省心，不用担心文件丢失或版本过时。本地文件方式需要额外维护，容易遗漏。

### 3. 拼写错误是隐形杀手

`ture` 这种拼写错误不会报错，但会让功能静默失效。改完配置后一定要在浏览器里实测每个功能点。

### 4. 国内推 GitHub，SSH 最稳

HTTPS 推送在国内网络环境下经常失败。配置 SSH 密钥是一次性操作，但换来的是长期稳定的推送体验。

### 5. 系统化排查比直觉靠谱

网站 404，第一反应是 DNS 问题。但排查后发现 DNS 完全正常，问题出在代码层面。从 DNS → HTTP 响应 → 代码审查，逐层排查才能定位到真正的根因。

---

## 附：仍待修复的中等问题

| # | 问题 | 说明 |
|---|------|------|
| 5 | coverpage 文件名不匹配 | 配置已修为 `true`，但 `_coverpage.md` 内容需检查 |
| 6 | `_sidebar.md` 链接失效 | 部分链接为空或指向不存在的文件 |
| 7 | `docs/README.md` 内容空洞 | 仅一行文字 |
| 8 | 文件名拼写 `taranslation` | 应为 `translation` |

---

*作者：雒文鹏 · 2026-06-01*
*博客：https://luowenpeng.com*
