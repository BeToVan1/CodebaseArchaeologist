import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../app/graph.css", import.meta.url), "utf8");
const page = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

test("desktop workspace bounds both sidebars instead of only the inspector", () => {
  const desktop = css.slice(css.indexOf("@media (min-width: 1001px)"), css.indexOf("/* Stacked sidebars"));
  assert.match(desktop, /\.shell\s*\{[^}]*height: 100dvh/);
  assert.match(desktop, /\.workspace\s*\{[^}]*min-height: 0[^}]*grid-template-rows: minmax\(0, 1fr\)/);
  assert.match(desktop, /\.rail, \.detail\s*\{[^}]*height: 100%[^}]*overflow-y: auto[^}]*overscroll-behavior-y: contain/);
  assert.match(desktop, /\.graph-surface\s*\{[^}]*height: auto/);
  assert.match(page, /className="rail" aria-label="Repository controls" tabIndex=\{0\}/);
  assert.match(page, /className="detail" aria-label="Selected code details" aria-live="polite" tabIndex=\{0\}/);
});

test("stacked sidebars use document scrolling on mobile and tablet", () => {
  assert.match(css, /@media \(max-width: 1000px\)\s*\{\s*\.rail, \.detail\s*\{ height: auto; max-height: none; overflow: visible;/);
});

test("prominent example notice is tied to loaded report origin, not a repository name", () => {
  assert.match(page, /graph && reportOrigin === "example" && <section className="example-report-banner"/);
  assert.match(page, /Example report · Cosmic Python/);
  assert.match(page, /not a repository you submitted/);
  assert.match(page, /choose Analyze repository to create your own map/);
});
