// The widget injects a second script into its iframe as a string; `node --check`
// can't see inside it, so evaluate the string builder and parse the result.
const fs = require('fs');

const src = fs.readFileSync(process.argv[2], 'utf8');
const match = src.match(/function employeeInnerScript\(\) \{\s*return ([\s\S]*?);\n  \}/);
if (!match) {
  console.error('employeeInnerScript() not found in', process.argv[2]);
  process.exit(1);
}
// eslint-disable-next-line no-eval
const inner = eval(match[1]);
new Function(inner); // throws SyntaxError on invalid code
console.log(`widget inner script OK (${inner.length} chars)`);
