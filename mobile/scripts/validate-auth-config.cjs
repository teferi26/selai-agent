const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

const mobileRoot = path.resolve(__dirname, "..");
const committedConfigFiles = ["app.json", "eas.json"];
const committedKeyPattern = /pk_(?:test|live)_[A-Za-z0-9_-]{12,}/;

for (const relativePath of committedConfigFiles) {
  const contents = fs.readFileSync(path.join(mobileRoot, relativePath), "utf8");
  assert.equal(
    committedKeyPattern.test(contents),
    false,
    `${relativePath} contains a committed Clerk publishable key`,
  );
}

const {
  PRODUCTION_API_URL,
  validateApiConfiguration,
  validateClerkConfiguration,
} = require("../app.config.js");
const testKey = ["pk", "test", "example_value"].join("_");
const liveKey = ["pk", "live", "example_value"].join("_");

assert.doesNotThrow(() => validateClerkConfiguration("development", ""));
assert.doesNotThrow(() => validateClerkConfiguration("development", testKey));
assert.doesNotThrow(() => validateClerkConfiguration("preview", testKey));
assert.doesNotThrow(() => validateClerkConfiguration("production", liveKey));
assert.throws(() => validateClerkConfiguration("development", liveKey));
assert.throws(() => validateClerkConfiguration("preview", ""));
assert.throws(() => validateClerkConfiguration("preview", liveKey));
assert.throws(() => validateClerkConfiguration("production", ""));
assert.throws(() => validateClerkConfiguration("production", testKey));

assert.doesNotThrow(() => validateApiConfiguration("development", ""));
assert.doesNotThrow(() =>
  validateApiConfiguration("development", "http://192.168.1.50:8000/"),
);
assert.equal(
  validateApiConfiguration("preview", "https://api-preview.selai.es/"),
  "https://api-preview.selai.es",
);
assert.equal(
  validateApiConfiguration("production", `${PRODUCTION_API_URL}/`),
  PRODUCTION_API_URL,
);
assert.throws(() => validateApiConfiguration("preview", ""));
assert.throws(() => validateApiConfiguration("preview", PRODUCTION_API_URL));
assert.throws(() => validateApiConfiguration("preview", "http://api-preview.selai.es"));
assert.throws(() => validateApiConfiguration("production", ""));
assert.throws(() =>
  validateApiConfiguration("production", "https://api-preview.selai.es"),
);

console.log("Authentication and API configuration are isolated by environment.");
