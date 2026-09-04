import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";
import test from "node:test";

const root = new URL("../", import.meta.url);
const read = path => readFileSync(new URL(path, root), "utf8");

test("every runtime Python COPY input exists, is staged, and is explicitly allowed by Docker", () => {
  const runtime = read("Dockerfile.deep-service").split("FROM runtime AS validation")[0];
  const rules = read("Dockerfile.deep-service.dockerignore").split(/\r?\n/).map(line => line.trim());
  const staging = read("scripts/Test-DeepService.ps1").split("$contextFiles = @(")[1].split("\n)")[0];
  const inputs = [...runtime.matchAll(/^COPY (.+) \/app\/$/gm)]
    .flatMap(match => match[1].split(/\s+/)).filter(path => path.endsWith(".py"));
  assert.ok(inputs.includes("project_discovery.py"));
  assert.ok(rules.includes("**"), "Keep default exclusion of unrelated files");
  for (const path of inputs) {
    assert.ok(statSync(new URL(path, root)).isFile(), `${path} must exist`);
    assert.ok(staging.includes(`'${path}'`), `${path} must be staged`);
    assert.ok(rules.includes(`!${path}`), `${path} must be explicitly allowed by Docker`);
  }
});
