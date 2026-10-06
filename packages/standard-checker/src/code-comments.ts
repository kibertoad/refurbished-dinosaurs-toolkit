// Reading the comments of C#, TypeScript, JavaScript and PowerShell source.

import type { CodeLine } from "./types.ts";

/**
 * Reads the comments of a source file line by line, choosing the reader by the file's extension:
 * C# (.cs), TypeScript and JavaScript (.ts, .js, .mjs) or PowerShell (.ps1). Returns undefined for
 * any other file.
 */
export function commentsOf(file: string, source: string): CodeLine[] | undefined {
  if (file.endsWith(".cs")) return codeComments(source, false);
  if (/\.(?:ts|js|mjs)$/.test(file)) return codeComments(source, true);
  if (file.endsWith(".ps1")) return powershellComments(source);
  return undefined;
}

/**
 * Returns line with each of its comments replaced by spaces, so what is left is its code, strings
 * included, at the columns it had. comments are what the comment reader found on that line.
 */
export function codeText(line: string, comments: CodeLine["comments"]): string {
  let out = line;
  for (const c of comments) {
    const end = Math.min(out.length, c.column + c.text.length);
    out = out.slice(0, c.column) + " ".repeat(Math.max(0, end - c.column)) + out.slice(end);
  }
  return out;
}

// The comments of a PowerShell script, line by line, in the same form as codeComments gives them.
// `#` starts a comment that runs to the end of the line where a new token may begin: at the start
// of a line, after whitespace, or after one of ; | & ( ) { } , =. Elsewhere, as in `a#b`, it is part
// of a word. `<# … #>` is a block comment, and the lines of one after its first are marked
// continued. Strings are skipped: '…' (with '' for a quote), "…" (with a backtick escape) and the
// here-strings @'…'@ and @"…"@, which close only at the start of a line.
/** Reads the comments of a PowerShell script line by line, as the comment above describes. */
export function powershellComments(source: string): CodeLine[] {
  const text = source.replace(/\r\n?/g, "\n");
  const lines: CodeLine[] = [{ code: false, comments: [] }];
  const line = () => lines[lines.length - 1]!;
  let i = 0,
    column = 0;
  const advance = (to: number) => {
    for (; i < to; i++) {
      if (text[i] === "\n") {
        lines.push({ code: false, comments: [] });
        column = 0;
      } else column++;
    }
  };
  const addComment = (from: number, to: number, col: number, continued = false) =>
    line().comments.push({ column: col, text: text.slice(from, to), continued });
  const tokenMayStart = () => i === 0 || /[\s;|&(){},=]/.test(text[i - 1]!);
  // i is past the opening quote; stops past the closing one. A doubled quote, or in a double-quoted
  // string a character after a backtick, does not close it.
  const skipString = (quote: string) => {
    while (i < text.length) {
      if (quote === '"' && text[i] === "`") advance(i + 2);
      else if (text[i] === quote && text[i + 1] === quote) advance(i + 2);
      else if (text[i] === quote) {
        advance(i + 1);
        return;
      } else advance(i + 1);
    }
  };
  while (i < text.length) {
    const c = text[i];
    if (c === "\n" || c === " " || c === "\t") {
      advance(i + 1);
      continue;
    }
    if (text.startsWith("<#", i)) {
      const close = text.indexOf("#>", i + 2);
      const end = close < 0 ? text.length : close + 2;
      let from = i,
        col = column,
        continued = false;
      while (i < end) {
        const nl = text.indexOf("\n", i);
        if (nl < 0 || nl >= end) {
          addComment(from, end, col, continued);
          advance(end);
          break;
        }
        addComment(from, nl, col, continued);
        advance(nl + 1);
        continued = true;
        while (i < end && (text[i] === " " || text[i] === "\t")) advance(i + 1);
        from = i;
        col = column;
      }
      continue;
    }
    if (c === "#" && tokenMayStart()) {
      const nl = text.indexOf("\n", i);
      const end = nl < 0 ? text.length : nl;
      addComment(i, end, column);
      advance(end);
      continue;
    }
    line().code = true;
    const here = /^@(['"])[ \t]*\n/.exec(text.slice(i, i + 64));
    if (here) {
      // A here-string closes at a line that starts with its quote and @.
      const close = new RegExp(`\\n${here[1]}@`, "g");
      close.lastIndex = i;
      const m = close.exec(text);
      advance(m ? m.index + m[0].length : text.length);
      continue;
    }
    if (c === "'" || c === '"') {
      advance(i + 1);
      skipString(c);
      continue;
    }
    advance(i + 1);
  }
  return lines;
}

// The comments of a C-family source file (C#, TypeScript, JavaScript), line by line: for each
// line, whether it holds code and the comment text on it with the column where each piece starts.
// String and character literals are skipped (regular, verbatim, interpolated and raw in C#;
// template literals in JavaScript), so a `//` inside one starts no comment. An interpolation hole is
// read as part of its string, which is enough to find comments. A JavaScript regular expression
// literal is skipped too, so a quote or a `/*` inside one starts nothing; a `/` is read as one
// where a value can begin. The lines of a `/* … */` after its first are marked continued.
/** Reads the comments of a C-family source file line by line, as the comment above describes. */
export function codeComments(source: string, javascript: boolean): CodeLine[] {
  const text = source.replace(/\r\n?/g, "\n");
  const lines: CodeLine[] = [{ code: false, comments: [] }];
  const line = () => lines[lines.length - 1]!;
  let i = 0,
    column = 0;
  const advance = (to: number) => {
    for (; i < to; i++) {
      if (text[i] === "\n") {
        lines.push({ code: false, comments: [] });
        column = 0;
      } else column++;
    }
  };
  const addComment = (from: number, to: number, col: number, continued = false) =>
    line().comments.push({ column: col, text: text.slice(from, to), continued });
  let lastCode = -1; // the index of the last character read as code
  // A JavaScript `/` starts a regular expression unless it follows a value, where it divides.
  const regexMayStart = () => {
    const before = text.slice(Math.max(0, lastCode - 11), lastCode + 1);
    return (
      lastCode < 0 ||
      !/[\w$)\]}]$/.test(before) ||
      /(?<![\w$])(?:return|typeof|case|do|else|in|of|new|delete|void|throw|instanceof|yield|await)$/.test(before)
    );
  };
  // Past the closing `/` of the regular expression literal at i, if it closes on its line.
  const regexEnd = () => {
    let inClass = false;
    for (let j = i + 1; j < text.length && text[j] !== "\n"; j++) {
      if (text[j] === "\\") {
        if (text[j + 1] === "\n") return -1;
        j++;
      } else if (text[j] === "[") inClass = true;
      else if (text[j] === "]") inClass = false;
      else if (text[j] === "/" && !inClass) return j + 1;
    }
    return -1;
  };
  // i is past the opening quote(s); stops past the closing one(s). A regular string ends at the
  // line when it is not closed.
  const skipString = (end: string, escapes: boolean, multiline: boolean) => {
    while (i < text.length) {
      if (escapes && text[i] === "\\") {
        advance(i + 2);
        continue;
      }
      if (text.startsWith(end, i)) {
        if (!escapes && end === '"' && text[i + 1] === '"') {
          advance(i + 2);
          continue;
        } // "" in a verbatim string
        advance(i + end.length);
        return;
      }
      if (text[i] === "\n" && !multiline) {
        advance(i + 1);
        return;
      }
      advance(i + 1);
    }
  };
  while (i < text.length) {
    const c = text[i];
    if (c === "\n" || c === " " || c === "\t") {
      advance(i + 1);
      continue;
    }
    if (text.startsWith("//", i)) {
      const nl = text.indexOf("\n", i);
      const end = nl < 0 ? text.length : nl;
      addComment(i, end, column);
      advance(end);
      continue;
    }
    if (text.startsWith("/*", i)) {
      const close = text.indexOf("*/", i + 2);
      const end = close < 0 ? text.length : close + 2;
      let from = i,
        col = column,
        continued = false;
      while (i < end) {
        const nl = text.indexOf("\n", i);
        if (nl < 0 || nl >= end) {
          addComment(from, end, col, continued);
          advance(end);
          break;
        }
        addComment(from, nl, col, continued);
        advance(nl + 1);
        continued = true;
        while (i < end && (text[i] === " " || text[i] === "\t")) advance(i + 1);
        from = i;
        col = column;
      }
      continue;
    }
    line().code = true;
    if (javascript) {
      const regex = c === "/" && regexMayStart() ? regexEnd() : -1;
      if (regex >= 0) advance(regex);
      else if (c === '"' || c === "'") {
        advance(i + 1);
        skipString(c, true, false);
      } else if (c === "`") {
        advance(i + 1);
        skipString("`", true, true);
      } else advance(i + 1);
      lastCode = i - 1;
      continue;
    }
    const prefix = /^(?:\$+@?|@\$*)?(?=")/.exec(text.slice(i, i + 4))?.[0] ?? null;
    if (prefix !== null) {
      advance(i + prefix.length);
      const quotes = /^"{3,}/.exec(text.slice(i, i + 64))?.[0];
      if (quotes) {
        advance(i + quotes.length);
        skipString(quotes, false, true);
      } else {
        const verbatim = prefix.includes("@");
        advance(i + 1);
        skipString('"', !verbatim, verbatim);
      }
      continue;
    }
    if (c === "'") {
      advance(i + 1);
      skipString("'", true, false);
      continue;
    }
    advance(i + 1);
  }
  return lines;
}
