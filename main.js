/**
 * main.js — 雒文鹏博客主脚本
 * 从 index.html 抽取，包含：Docsify 配置、插件、路由拦截
 * 加载顺序：style.css → article-meta.js → main.js → docsify.min.js → 其他 CDN 插件
 */

// ============================================================
// 1. Docsify 路由拦截：类比修辞收集库跳转到独立页面
//    必须在 Docsify 加载之前执行
// ============================================================
(function() {
  var targetPath = '/analogy-collection.html';
  // 直接访问 #/analogy-collection.html 时，在 Docsify 加载前立即重定向
  var hash = window.location.hash;
  if (hash && hash.indexOf('analogy-collection.html') !== -1) {
    window.location.replace(targetPath);
  }
  // 点击链接时拦截（capture 阶段，优先于 Docsify）
  document.addEventListener('click', function(e) {
    var a = e.target.closest('a');
    if (a && a.getAttribute('href') && a.getAttribute('href').indexOf('analogy-collection.html') !== -1) {
      e.preventDefault();
      e.stopPropagation();
      window.location.href = targetPath;
    }
  }, true);
  // Docsify 通过 hashchange 处理路由，同样需要拦截
  window.addEventListener('hashchange', function() {
    var h = window.location.hash;
    if (h && h.indexOf('analogy-collection.html') !== -1) {
      window.location.replace(targetPath);
    }
  });
})();

// ============================================================
// 2. Docsify 配置 + 插件
// ============================================================
window.$docsify = {
  el: '#app',
  repo: 'https://github.com/luowenpeng/luowenpeng.github.io/',
  loadNavbar: true,
  name: 'HOME',
  nameLink: '/',
  mergeNavbar: false,
  maxLevel: 2,
  subMaxLevel: 3,

  //coverpage
  coverpage: true,

  //search
  search: 'auto',

  plugins: [
    // 插件：将 LaTeX 定界符 \(...\) \[...\] 转换为 $...$ $$...$$
    // docsify-katex 只支持 $ 定界符，需在 Markdown 解析前转换
    function(hook) {
      hook.beforeEach(function(content) {
        // 保护代码块，避免误转换
        var codeBlocks = [];
        content = content.replace(/```[\s\S]*?```/g, function(match) {
          codeBlocks.push(match);
          return '\x00CB' + (codeBlocks.length - 1) + '\x00';
        });

        // \[...\] → $$...$$（块级公式）
        content = content.replace(/\\\[/g, '$$').replace(/\\\]/g, '$$');
        // \(...\) → $...$（行内公式）
        content = content.replace(/\\\(/g, '$').replace(/\\\)/g, '$');

        // 恢复代码块
        content = content.replace(/\x00CB(\d+)\x00/g, function(match, idx) {
          return codeBlocks[parseInt(idx)];
        });

        return content;
      });
    },

    // 插件：在每篇文章 h1 标题下方注入发布日期和更新日期
    function(hook) {
      hook.doneEach(function() {
        var hash = window.location.hash;
        var route = hash.replace(/^#\/?/, '');
        if (!route || route === '/') return;

        // 兼容带/不带 .md 后缀的 hash 路由
        var meta = window.$articleMeta[route] || window.$articleMeta[route + '.md'];
        if (!meta) return;

        var h1 = document.querySelector('.markdown-section h1');
        if (!h1) return;

        // 防止重复注入
        var existing = document.querySelector('.article-dates');
        if (existing) existing.remove();

        var sameDay = (meta.published === meta.modified);
        var dateHtml = '<span class="article-date-pub">发布于 ' + meta.published + '</span>';
        if (!sameDay) {
          dateHtml += '<span class="article-date-mod">更新于 ' + meta.modified + '</span>';
        }

        var dateDiv = document.createElement('div');
        dateDiv.className = 'article-dates';
        dateDiv.innerHTML = dateHtml;
        h1.insertAdjacentElement('afterend', dateDiv);
      });
    },

    // 插件：在侧边栏底部注入不蒜子访问量计数器
    // 防刷策略：sessionStorage 同会话去重 + localStorage 1h 冷却缓存
    function(hook) {
      hook.doneEach(function() {
        if (document.getElementById('busuanzi_container_site_pv')) return;

        var sidebarNav = document.querySelector('.sidebar-nav');
        if (!sidebarNav) return;

        var counterSpan = document.createElement('span');
        counterSpan.id = 'busuanzi_container_site_pv';
        counterSpan.innerHTML = '统计始于：2026.6.15 累计👀 <span id="busuanzi_value_site_pv" style="font-weight:600;color:#5b7aa2;">…</span> 次访问';
        counterSpan.style.display = 'block';
        counterSpan.style.padding = '12px 0 0 0';
        counterSpan.style.fontSize = '0.85em';
        counterSpan.style.color = '#999';
        counterSpan.style.textAlign = 'center';
        sidebarNav.appendChild(counterSpan);

        var CACHE_KEY = '_bsz_cache';
        var CACHE_TS_KEY = '_bsz_cache_ts';
        var COUNTED_KEY = '_bsz_counted';
        var COOLDOWN_MS = 60 * 60 * 1000; // 1 小时冷却

        function fillDisplay(val) {
          var el = document.getElementById('busuanzi_value_site_pv');
          if (el) el.textContent = val;
        }

        function showCached() {
          try {
            var cached = localStorage.getItem(CACHE_KEY);
            if (cached !== null && cached !== 'undefined') {
              fillDisplay(cached);
              return true;
            }
          } catch(e) {}
          return false;
        }

        // 1. 同会话已计过数 → 直接用缓存
        if (sessionStorage.getItem(COUNTED_KEY)) {
          showCached() || fillDisplay('—');
          return;
        }

        // 2. 检查冷却：缓存时间在 1h 内 → 展示缓存值，不调 API
        try {
          var cachedTs = localStorage.getItem(CACHE_TS_KEY);
          if (cachedTs && (Date.now() - parseInt(cachedTs, 10)) < COOLDOWN_MS) {
            showCached() || fillDisplay('—');
            return;
          }
        } catch(e) {}

        // 3. 需要真正计数 → JSONP 调不蒜子 API
        sessionStorage.setItem(COUNTED_KEY, '1');
        var timer = null;

        function onApiResult(data) {
          var val = (data && data.site_pv != null) ? String(data.site_pv) : '—';
          fillDisplay(val);
          if (timer) clearTimeout(timer);
          // 缓存到 localStorage
          try {
            localStorage.setItem(CACHE_KEY, val);
            localStorage.setItem(CACHE_TS_KEY, String(Date.now()));
          } catch(e) {}
        }

        function doFetch(attempt) {
          var cbName = 'bsz_cb_' + attempt + '_' + Date.now();
          window[cbName] = function(data) {
            onApiResult(data);
            try { delete window[cbName]; } catch(e) {}
            var t = document.getElementById('bsz_jsonp_tag');
            if (t) t.remove();
          };

          if (!timer) {
            timer = setTimeout(function() {
              var el = document.getElementById('busuanzi_value_site_pv');
              if (el && el.textContent === '…') {
                if (attempt < 2) { doFetch(attempt + 1); return; }
                fillDisplay('—');
              }
            }, 8000);
          }

          var s = document.createElement('script');
          s.id = 'bsz_jsonp_tag';
          s.src = 'https://busuanzi.ibruce.info/busuanzi?jsonpCallback=' + cbName + '&_t=' + Date.now();
          document.head.appendChild(s);
        }

        doFetch(1);
      });
    }
  ]
};
