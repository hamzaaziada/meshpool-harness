function slugify(s) {
  return s.toLowerCase().replace(" ", "-");
}
module.exports = { slugify };
