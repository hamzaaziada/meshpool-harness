const test = require("node:test");
const assert = require("node:assert");
const { slugify } = require("./slug");
test("spaces", () => assert.strictEqual(slugify("Hello Big World"), "hello-big-world"));
test("punctuation", () => assert.strictEqual(slugify("Hi, there!"), "hi-there"));
