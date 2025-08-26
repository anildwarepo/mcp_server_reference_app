import DOMPurify from "dompurify";

export function SafeHTML({ html }: { html: string }) {
  const clean = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
  return <div dangerouslySetInnerHTML={{ __html: clean }} />;
}

export const isHtml = (s: string) => /<([a-z][\s\S]*?)>/i.test(s);