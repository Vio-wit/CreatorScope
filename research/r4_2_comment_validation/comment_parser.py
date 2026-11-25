"""Privacy-preserving DOM helpers for the R4.2 visible-page comment probe.

The parser reads only rendered page DOM. It deliberately excludes commenter
names, account links, avatars and IDs from its returned schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class CommentSelectors:
    """Selectors confirmed against the live page before collection."""

    item: str
    text: str
    time: str | None = None
    likes: str | None = None
    replies: str | None = None
    pinned: str | None = None
    reply_toggle: str | None = None


def dom_outline(page) -> dict[str, Any]:
    """Return structural metadata only; never returns arbitrary page text."""
    return page.evaluate(
        """() => {
          const visible = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const classes = (el) => typeof el.className === 'string' ? el.className : '';
          const all = [...document.querySelectorAll('body *')].filter(visible);
          const related = all.filter(el => /(comment|reply|pin)/i.test(classes(el)) ||
              /(comment|reply|pin)/i.test(el.getAttribute('aria-label') || ''));
          const counts = new Map();
          for (const el of related) {
            const key = `${el.tagName.toLowerCase()}${classes(el) ? '.' + classes(el).trim().replace(/\\s+/g,'.') : ''}`;
            counts.set(key, (counts.get(key) || 0) + 1);
          }
          const candidates = [...counts].map(([selector, count]) => ({selector, count}));
          const scrollables = all.filter(el => el.scrollHeight - el.clientHeight > 120)
            .map(el => { const r=el.getBoundingClientRect(); return {
              tag:el.tagName.toLowerCase(), cls:classes(el).slice(0,160),
              role:el.getAttribute('role'), x:Math.round(r.x), y:Math.round(r.y),
              w:Math.round(r.width), h:Math.round(r.height),
              clientHeight:el.clientHeight, scrollHeight:el.scrollHeight,
              scrollTop:el.scrollTop
            }; }).slice(0,80);
          const safeHeadings = new Set(['评论','精选评论','全部评论','评论区','回复','最热','最新','展开','收起','更多']);
          const headings = all.filter(el => safeHeadings.has((el.innerText || '').trim()))
            .map(el => ({tag:el.tagName.toLowerCase(),cls:classes(el).slice(0,160),
              text:(el.innerText||'').trim(), x:Math.round(el.getBoundingClientRect().x),
              y:Math.round(el.getBoundingClientRect().y)})).slice(0,80);
          const itemLike = related.filter(el => /(comment-item|commentitem|parent-comment|comment-card)/i.test(classes(el)));
          const textShapes = itemLike.slice(0,12).map(root => {
            const rows=[];
            const walker=document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
            let n;
            while((n=walker.nextNode()) && rows.length<80) {
              const t=(n.nodeValue||'').trim(); if (!t) continue;
              let p=n.parentElement, chain=[];
              for(let i=0;p&&p!==root&&i<5;i++,p=p.parentElement) chain.push({tag:p.tagName.toLowerCase(),cls:classes(p).slice(0,140)});
              rows.push({length:t.length,chain});
            }
            return {rootTag:root.tagName.toLowerCase(),rootClass:classes(root).slice(0,180),
              rect:(()=>{const r=root.getBoundingClientRect();return{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}})(), textNodes:rows};
          });
          return {urlPath:location.pathname,title:document.title,viewport:{w:innerWidth,h:innerHeight},
            commentRelatedClassCounts:candidates,scrollables,headings,textShapes};
        }"""
    )


def extract_comments(
    page,
    note_id: str,
    capture_time: str,
    selectors: CommentSelectors,
    *,
    limit: int = 10,
    comment_level: int = 1,
) -> list[dict[str, Any]]:
    """Extract visible comment fields from a confirmed, scoped comment row selector."""
    result = page.evaluate(
        """({itemSelector,textSelector,timeSelector,likesSelector,repliesSelector,pinnedSelector,noteId,captureTime,limit,level}) => {
          const visible = (el) => {
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
          };
          const text = (root, selector) => {
            if (!selector) return null;
            const el = root.matches(selector) ? root : root.querySelector(selector);
            if (!el || !visible(el)) return null;
            const value=(el.innerText || el.textContent || '').trim();
            if ((selector===likesSelector && ['赞','Like'].includes(value)) ||
                (selector===repliesSelector && ['回复','Reply'].includes(value))) return null;
            return value || null;
          };
          const rows = [...document.querySelectorAll(itemSelector)].filter(visible);
          return rows.slice(0,limit).map((root) => {
            const body=text(root,textSelector);
            if (!body) return null;
            const pinnedText = text(root,pinnedSelector);
            const parent = level===2 ? root.closest('.parent-comment')?.querySelector(':scope > .comment-item:not(.comment-item-sub)') : null;
            return {note_id:noteId,source:'xiaohongshu',comment_text:body,
              displayed_time:text(root,timeSelector),like_count_raw:text(root,likesSelector),
              reply_count_raw:text(root,repliesSelector),
              is_pinned:pinnedText ? true : (pinnedSelector ? false : null),comment_level:level,parent_comment_id:null,
              parent_comment_text:parent?text(parent,textSelector):null,
              parent_displayed_time:parent?text(parent,timeSelector):null,
              capture_time:captureTime};
          }).filter(Boolean);
        }""",
        {
            "itemSelector": selectors.item,
            "textSelector": selectors.text,
            "timeSelector": selectors.time,
            "likesSelector": selectors.likes,
            "repliesSelector": selectors.replies,
            "pinnedSelector": selectors.pinned,
            "noteId": note_id,
            "captureTime": capture_time,
            "limit": max(0, int(limit)),
            "level": int(comment_level),
        },
    )
    return result


def local_comment_id(record: dict[str, Any]) -> str:
    """Create an intra-batch fingerprint, not a verified cross-batch ID.

    Relative display times may change between captures, while identical text
    by different commenters may collide because commenter identity is omitted.
    """
    key = "\x1f".join(
        str(record.get(name) or "")
        for name in ("note_id", "comment_text", "displayed_time", "comment_level", "parent_comment_id")
    )
    return sha256(key.encode("utf-8")).hexdigest()[:20]


def now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
