---
"@scientific-method/standard-checker": patch
---

Skip `dist` directories when walking the code, reference and rebuild directories, as `bin`, `obj` and `node_modules` already are. A file name that only a build of a package has, such as `index.js`, no longer counts as a rebuild source file the spec may not name, and addresses in bundled output are no longer checked against comments.
