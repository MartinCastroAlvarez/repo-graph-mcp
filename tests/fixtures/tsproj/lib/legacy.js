const { slugify } = require("../src/util");
const path = require("path");

function run(name) {
  return path.join("out", slugify(name));
}

module.exports = { run };
