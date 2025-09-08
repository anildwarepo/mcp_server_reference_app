// safehtml.tsx
import { useMemo } from "react";
import DOMPurify, { type Config as DOMPurifyConfig } from "dompurify";

const PURIFY_OPTS: DOMPurifyConfig = {
  USE_PROFILES: { html: true },
  ALLOWED_ATTR: ["href","title","alt","target","rel","class","id","role","aria-label","aria-describedby","colspan","rowspan","scope"],
};

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node instanceof Element && node.tagName === "A" && node.getAttribute("target") === "_blank") {
    const rel = (node.getAttribute("rel") || "").split(/\s+/);
    for (const req of ["noopener","noreferrer"]) if (!rel.includes(req)) rel.push(req);
    node.setAttribute("rel", rel.filter(Boolean).join(" "));
  }
});

export const sanitizeHtml = (html: string) => DOMPurify.sanitize(html, PURIFY_OPTS);

export function SafeHTML({ html }: { html: string }) {
  const clean = useMemo(() => sanitizeHtml(html), [html]);
  return <div dangerouslySetInnerHTML={{ __html: clean }} />;
}

export const isHtml = (s: string) => /<\/?[a-z][\w:-]*(?:\s+[^>]*?)?>/i.test(s);
export const hasTable = (s: string) => /<\/?table\b/i.test(s);
