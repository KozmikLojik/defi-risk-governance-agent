const fs = require('fs');

function secret(name) {
  const file = process.env[`${name}_FILE`];
  if (!file) return process.env[name];
  let stat;
  try {
    stat = fs.statSync(file);
  } catch (error) {
    if (error.code === 'ENOENT') {
      throw new Error(`${name}_FILE points to a missing file (${file}). Edit the matching path in the project-root .env file.`);
    }
    throw error;
  }
  if (!stat.isFile() || stat.size > 16_384) throw new Error(`${name}_FILE is not a small regular file`);
  return fs.readFileSync(file, 'utf8').trim();
}

module.exports = { secret };
