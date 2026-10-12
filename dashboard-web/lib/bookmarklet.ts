/** Builds the "save to SourceHero" bookmarklet for a given dashboard origin.
 *
 * The bookmarklet opens /app/capture with the current page's URL, title and text
 * selection, each encoded with encodeURIComponent. The origin is embedded in the
 * code as a string literal, so it is validated to contain no quotes or whitespace.
 */

const ORIGIN_PATTERN = /^https?:\/\/[^\s'"\\<>]+$/;

export function buildBookmarklet(origin: string): string {
  const base = origin.replace(/\/$/, "");
  if (!ORIGIN_PATTERN.test(base)) {
    throw new Error(`Bookmarklet origin must be an http(s) URL: ${origin}`);
  }
  const code =
    "(function(){var s=String(window.getSelection()||'');" +
    `window.open('${base}/app/capture?url='+encodeURIComponent(location.href)+` +
    "'&title='+encodeURIComponent(document.title)+'&text='+encodeURIComponent(s),'_blank');})();";
  return `javascript:${code}`;
}
