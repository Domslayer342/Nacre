"""Reading mode: pull the article out of a page and show it in a themed, distraction-free overlay."""
import json

# Scores block containers by paragraph text (minus link-heavy text), cleans the winner and renders it inside a
# shadow root so site CSS can't leak in. Calling it again closes the reader.
READER_JS = r"""(() => {
  const old = document.getElementById('__nacre_reader');
  if (old) { old.remove(); document.documentElement.style.overflow = ''; return 'closed'; }
  const V = %VARS%;
  const text = e => (e.innerText || '').trim();
  const linkDensity = e => { const t = text(e).length || 1;
    return [...e.querySelectorAll('a')].reduce((n, a) => n + text(a).length, 0) / t; };
  let best = document.querySelector('article') || null, bestScore = best ? text(best).length * 1.5 : 0;
  for (const el of document.querySelectorAll('main, article, section, div, [role=main]')) {
    const ps = el.querySelectorAll(':scope > p, :scope > div > p, :scope > section > p');
    if (ps.length < 2) continue;
    let s = 0; ps.forEach(p => { const n = text(p).length; if (n > 40) s += n + 100 * (text(p).match(/,/g) || []).length; });
    s *= (1 - linkDensity(el));
    if (/comment|footer|sidebar|nav|related|promo|share/i.test(el.className + ' ' + el.id)) s *= 0.3;
    if (s > bestScore) { best = el; bestScore = s; }
  }
  if (!best || text(best).length < 400) return 'none';
  const c = best.cloneNode(true);
  c.querySelectorAll('script,style,noscript,iframe,form,button,input,select,nav,aside,footer,header,svg,'
    + '[aria-hidden=true],[role=complementary],[class*=share],[class*=social],[class*=related],[class*=newsletter],'
    + '[class*=advert],[class*=promo],[id*=comments],[class*=comments],[class*=editsection],[class*=noprint],'
    + '.mw-jump-link,.reference,sup.reference,[class*=navbox],[role=navigation]').forEach(e => e.remove());
  c.querySelectorAll('img').forEach(i => { const s = i.dataset.src || i.dataset.lazySrc || i.getAttribute('data-original');
    if (s) i.src = s; i.removeAttribute('srcset'); i.removeAttribute('width'); i.removeAttribute('height'); i.loading = 'lazy'; });
  c.querySelectorAll('*').forEach(e => { e.removeAttribute('style'); e.removeAttribute('class'); e.removeAttribute('id'); });
  const h1 = document.querySelector('h1');
  const title = (h1 && h1.innerText.trim()) || (document.querySelector('meta[property="og:title"]') || {}).content
    || document.title;
  const by = (document.querySelector('meta[name=author]') || {}).content || '';
  const site = (document.querySelector('meta[property="og:site_name"]') || {}).content || location.hostname;
  const words = text(c).split(/\s+/).length, mins = Math.max(1, Math.round(words / 230));
  const host = document.createElement('div'); host.id = '__nacre_reader';
  host.style.cssText = 'position:fixed;inset:0;z-index:2147483647';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `<style>
    :host{all:initial} .r{position:fixed;inset:0;overflow:auto;background:${V.bg};color:${V.fg};
      font:19px/1.7 'Rubik',system-ui,sans-serif;animation:in .35s cubic-bezier(.05,.7,.1,1)}
    @keyframes in{from{opacity:0;transform:translateY(24px)}}
    .bar{position:sticky;top:0;display:flex;gap:8px;justify-content:flex-end;padding:12px 16px;background:${V.bg}}
    .bar button{border:0;height:40px;min-width:40px;padding:0 14px;border-radius:20px;background:${V.high};color:${V.fg};
      font:500 14px 'Rubik',sans-serif;cursor:pointer;transition:border-radius .35s cubic-bezier(.38,1.21,.22,1)}
    .bar button:hover{background:${V.highest}} .bar button:active{border-radius:10px}
    .bar .i{font-family:'Material Symbols Rounded';font-size:20px;padding:0 10px}
    .c{max-width:var(--w,720px);margin:0 auto;padding:8px 24px 120px}
    .site{color:${V.p};font-weight:500;font-size:14px;letter-spacing:.02em;text-transform:uppercase}
    h1.t{font-size:2.1em;line-height:1.2;font-weight:500;margin:.3em 0 .2em}
    .meta{color:${V.var};font-size:15px;margin-bottom:2em}
    a{color:${V.p}} img,video,figure{max-width:100%;height:auto;border-radius:16px} figure{margin:1.5em 0}
    figcaption{color:${V.var};font-size:14px} pre,code{background:${V.high};border-radius:8px;font-size:.85em}
    pre{padding:14px;overflow:auto} blockquote{margin:1.2em 0;padding:.2em 1.2em;border-left:4px solid ${V.p};color:${V.var}}
    h2,h3{line-height:1.3} table{border-collapse:collapse} td,th{border:1px solid ${V.outline};padding:6px}
    .serif .c{font-family:Georgia,'Noto Serif',serif}
  </style><div class="r"><div class="bar">
    <button data-a="font">Aa</button><button class="i" data-a="smaller">text_decrease</button>
    <button class="i" data-a="bigger">text_increase</button><button class="i" data-a="width">width</button>
    <button class="i" data-a="close" title="Exit reading mode (Esc)">close</button></div>
    <div class="c"><div class="site"></div><h1 class="t"></h1><div class="meta"></div><div class="body"></div></div></div>`;
  root.querySelector('.site').textContent = site;
  root.querySelector('.t').textContent = title;
  root.querySelector('.meta').textContent = [by, mins + ' min read'].filter(Boolean).join(' · ');
  root.querySelector('.body').append(...c.childNodes);
  const r = root.querySelector('.r'); let size = 19, wide = false;
  const close = () => { host.remove(); document.documentElement.style.overflow = ''; removeEventListener('keydown', key, true); };
  const key = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  addEventListener('keydown', key, true);
  root.querySelector('.bar').onclick = e => { const a = e.target.dataset.a;
    if (a === 'close') close();
    if (a === 'font') r.classList.toggle('serif');
    if (a === 'bigger' || a === 'smaller') { size = Math.max(14, Math.min(28, size + (a === 'bigger' ? 1 : -1))); r.style.fontSize = size + 'px'; }
    if (a === 'width') { wide = !wide; r.style.setProperty('--w', wide ? '960px' : '720px'); } };
  document.documentElement.style.overflow = 'hidden';
  document.documentElement.append(host);
  return 'opened';
})()"""


def reader_js(css_vars):
    return READER_JS.replace("%VARS%", json.dumps(css_vars))
