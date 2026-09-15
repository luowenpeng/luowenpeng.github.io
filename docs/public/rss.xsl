<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <xsl:output method="html" encoding="UTF-8" indent="yes"/>
  <xsl:template match="/">
    <html lang="zh-CN">
      <head>
        <meta charset="UTF-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title><xsl:value-of select="rss/channel/title"/> — RSS 订阅</title>
        <style>
          * { margin: 0; padding: 0; box-sizing: border-box; }
          body {
            font-family: "Segoe UI", "Microsoft YaHei", -apple-system, sans-serif;
            background: #f6f8fa; color: #213547;
            max-width: 720px; margin: 0 auto; padding: 48px 20px;
            line-height: 1.7;
          }
          .card {
            background: #fff; border-radius: 12px; padding: 32px;
            box-shadow: 0 1px 4px rgba(33,53,71,.08);
          }
          h1 { font-size: 22px; color: #213547; margin-bottom: 8px; }
          h1 .rss-badge {
            display: inline-block; background: #3d5a80; color: #fff;
            font-size: 12px; padding: 2px 10px; border-radius: 99px;
            vertical-align: middle; margin-right: 8px; letter-spacing: 1px;
          }
          .desc { color: #5a6b7d; font-size: 14px; margin-bottom: 20px; }
          .notice {
            background: rgba(61,90,128,.08); border-left: 3px solid #3d5a80;
            padding: 14px 16px; border-radius: 6px; font-size: 14px; margin-bottom: 28px;
          }
          .notice code {
            background: #eef2f6; padding: 2px 8px; border-radius: 4px;
            font-size: 13px; color: #3d5a80; word-break: break-all;
          }
          .item {
            padding: 14px 0; border-bottom: 1px solid #eef2f6;
          }
          .item:last-child { border-bottom: none; }
          .item a {
            color: #3d5a80; text-decoration: none; font-size: 15px; font-weight: 600;
          }
          .item a:hover { text-decoration: underline; }
          .item .date { color: #8fa9c7; font-size: 12px; margin-top: 2px; }
          .item .summary { color: #5a6b7d; font-size: 13px; margin-top: 4px; }
          footer { text-align: center; margin-top: 28px; font-size: 13px; color: #8fa9c7; }
          footer a { color: #5b7aa2; text-decoration: none; }
        </style>
      </head>
      <body>
        <div class="card">
          <h1><span class="rss-badge">RSS</span><xsl:value-of select="rss/channel/title"/></h1>
          <p class="desc"><xsl:value-of select="rss/channel/description"/></p>
          <div class="notice">
            这是本站的 <strong>RSS 订阅源</strong>。请将下面的地址复制到你的 RSS 阅读器
            （如 Feedly、inoreader、NetNewsWire 等）即可订阅更新：<br/>
            <code>https://luowenpeng.com/feed.xml</code>
          </div>
          <xsl:for-each select="rss/channel/item">
            <div class="item">
              <a>
                <xsl:attribute name="href"><xsl:value-of select="link"/></xsl:attribute>
                <xsl:value-of select="title"/>
              </a>
              <div class="date"><xsl:value-of select="pubDate"/></div>
              <xsl:if test="description != ''">
                <div class="summary"><xsl:value-of select="description"/></div>
              </xsl:if>
            </div>
          </xsl:for-each>
          <footer>
            <a href="https://luowenpeng.com">← 返回 luowenpeng.com</a>
          </footer>
        </div>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
