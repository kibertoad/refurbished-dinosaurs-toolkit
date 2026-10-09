// Checks that look across entries: what the glossary and entry bodies cite, paths into the
// original's data, enumeration names, saves and recordings, value files, superseded_by chains and
// split entries.

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Context } from "../context.ts";
import { isSuperseded, whyMissing } from "../evidence.ts";
import { asList, idsIn } from "../ids.ts";
import { readText } from "../markdown.ts";
import { KINDS } from "../standard.ts";
import type { FormatNames } from "./formats.ts";

/** Runs the checks across entries, in order. */
export function checkAcrossEntries(ctx: Context, { enumNames }: FormatNames) {
  const { problem, config } = ctx;
  const { specDir } = config;
  const { entries, glossary, glossaryFiles, glossaryDir, buildFiles, otherFiles } = ctx.spec;
  const glossaryFile = (term: string) => glossaryFiles.get(term) ?? glossaryDir;

  // Glossary claims
  for (const [term, text] of glossary) {
    for (const x of idsIn(text))
      if (!entries.has(x)) problem(glossaryFile(term), `${term} cites ${x}, ${whyMissing(ctx, x)}`);
      else if (isSuperseded(entries, x)) problem(glossaryFile(term), `${term} cites ${x}, which is superseded`);
  }

  // Body references
  for (const [, e] of entries)
    for (const x of idsIn(e.body)) if (!entries.has(x)) problem(e.file, `the body names ${x}, ${whyMissing(ctx, x)}`);

  // A path into a build's data directories names, with its exact case, a file of some build's
  // manifest or a path of some build's list of other files. A directory, or a pattern whose last
  // part holds a placeholder such as nn or xxx, is left alone. The data directories are the
  // top-level directories of the manifests' files unless --data-dirs names them; the lists of other
  // files add no data directory. A directory exclusion accounts for the files under it in the
  // inventory, but a citation of one of those files passes only where the list also gives that file
  // by its own path, since the exclusion does not show that the file exists. A build path that holds
  // a space is matched whole, longest first, together with any path that follows it, so
  // Dir/With Space/file.ext is read as one path.
  {
    const exact = new Set<string>();
    // Folded path -> the path as written and the file that writes it.
    const folded = new Map<string, { path: string; where: string }>();
    const topDirs = new Set<string>();
    // Folded directory -> the directory as written and the file whose path gives it.
    const dirsFolded = new Map<string, { path: string; where: string }>();
    const exclusions: { dir: string; list: string }[] = [];
    const addDir = (dir: string, where: string) => {
      exact.add(dir);
      if (!dirsFolded.has(dir.toLowerCase())) dirsFolded.set(dir.toLowerCase(), { path: dir, where });
    };
    const addParents = (parts: string[], where: string) => {
      for (let i = 1; i < parts.length; i++) addDir(parts.slice(0, i).join("/"), where);
    };
    for (const files of buildFiles.values())
      for (const f of files) {
        const p = f.path;
        if (typeof p !== "string") continue;
        exact.add(p);
        folded.set(p.toLowerCase(), { path: p, where: "the build entry" });
        const parts = p.split("/");
        if (parts.length > 1) topDirs.add(parts[0]);
        addParents(parts, "the build entry");
      }
    for (const [id, paths] of otherFiles) {
      const list = `${id}.other-files.yaml`;
      for (const o of paths) {
        const isDir = o.endsWith("/");
        const p = isDir ? o.slice(0, -1) : o;
        if (!p) continue;
        if (isDir) {
          addDir(p, list);
          exclusions.push({ dir: o, list });
        } else {
          exact.add(p);
          if (!folded.has(p.toLowerCase())) folded.set(p.toLowerCase(), { path: p, where: list });
        }
        addParents(p.split("/"), list);
      }
    }
    const dataDirs = config.dataDirs ?? [...topDirs].sort();
    const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const tail = String.raw`[A-Za-z0-9_./-]*[A-Za-z0-9]`;
    const spaced = [...exact]
      .filter((p) => p.includes(" ") && dataDirs.includes(p.split("/")[0]))
      .sort((a, b) => b.length - a.length);
    const whole = spaced.length ? `(?:${spaced.map(escapeRe).join("|")})(?:${tail})?|` : "";
    const dataPath = new RegExp(String.raw`(?<!\w)(?:${whole}(?:${dataDirs.map(escapeRe).join("|")})\/${tail})`, "g");
    const checkPaths = (file: string, text: string) => {
      for (const m of text.matchAll(dataPath)) {
        const p = m[0];
        if (exact.has(p)) continue;
        const last = p.split("/").pop()!;
        const written = folded.get(p.toLowerCase());
        const dir = dirsFolded.get(p.toLowerCase());
        if (written) problem(file, `path ${p} is written ${written.path} in ${written.where}`);
        else if (dir) problem(file, `directory ${p} is written ${dir.path} in ${dir.where}`);
        else if (/\d/.test(last) && !/nn|NN|xx|XX/.test(last)) {
          const under = exclusions.find((x) => p.startsWith(x.dir));
          const underFolded = under ?? exclusions.find((x) => p.toLowerCase().startsWith(x.dir.toLowerCase()));
          problem(
            file,
            under
              ? `path ${p} lies under the directory exclusion ${under.dir} of ${under.list}, which does not name the files under it; list ${p} itself among the other files`
              : underFolded
                ? `path ${p} writes the directory exclusion ${underFolded.dir} of ${underFolded.list} in another case, and the exclusion does not name the files under it; list the path itself among the other files with its exact case`
                : `path ${p} is in no build's manifest or list of other files`,
          );
        }
      }
    };
    if (dataDirs.length > 0) {
      for (const [, e] of entries) if (e.kind !== "BLD") checkPaths(e.file, readFileSync(e.file, "utf8"));
      for (const [term, text] of glossary) if (glossaryFiles.has(term)) checkPaths(glossaryFiles.get(term)!, text);
    }
  }

  // Enumeration names are unique apart from split formats
  for (const [name, fmts] of enumNames) {
    const uniq = [...new Set(fmts)];
    if (
      uniq.length > 1 &&
      !uniq.every((f) => uniq.every((g) => f === g || asList(entries.get(f)!.meta.split_with).includes(g)))
    )
      problem(null, `enumeration name ${name} is defined by ${uniq.join(", ")}`);
  }

  // Saves and recordings must be listed in spec/LICENSE
  {
    const licence = existsSync(join(specDir, "LICENSE")) ? readFileSync(join(specDir, "LICENSE"), "utf8") : "";
    if (!licence) problem(null, "spec/LICENSE is missing");
    for (const sub of ["saves", "recordings"]) {
      const d = join(specDir, "experiments", sub);
      if (!existsSync(d)) continue;
      for (const f of readdirSync(d)) {
        if (f === ".gitkeep" || f.endsWith(".patch.json")) continue;
        if (!licence.includes(`experiments/${sub}/${f}`))
          problem(join(d, f), "is not listed in spec/LICENSE as covered by neither licence");
      }
    }
  }

  // Every save and recording is named by some experiment.
  {
    const named = new Set<string>();
    for (const e of entries.values())
      if (e.kind === "EXP")
        for (const v of [e.meta.starting_state, e.meta.recording]) if (typeof v === "string") named.add(v);
    for (const sub of ["saves", "recordings"]) {
      const d = join(specDir, "experiments", sub);
      if (existsSync(d))
        for (const f of readdirSync(d))
          if (f !== ".gitkeep" && !named.has(`${sub}/${f}`)) problem(join(d, f), "is named by no experiment");
    }
  }

  // Every value file belongs to the entry its name gives, in that entry's directory, and is named by it.
  for (const { dir } of Object.values(KINDS)) {
    const d = join(specDir, dir);
    if (!existsSync(d)) continue;
    for (const name of readdirSync(d)) {
      if (!name.endsWith(".csv")) continue;
      const m = /^([A-Z]+-[A-Z0-9]+-\d{3,})\.[A-Za-z0-9_]+\.csv$/.exec(name);
      const owner = m && entries.get(m[1]);
      if (!m || !owner || owner.file !== join(d, `${m[1]}.md`)) {
        problem(join(d, name), "belongs to no entry; a value file is named <ID>.<table>.csv and sits beside its entry");
        continue;
      }
      if (!readText(owner.file).includes(name)) problem(join(d, name), `is not named by ${m[1]}`);
    }
  }

  // No chain of superseded_by links leads back to where it started.
  for (const [id, e] of entries) {
    const seen = new Set<string>();
    const stack: string[] = [...asList(e.meta.superseded_by)];
    while (stack.length) {
      const x = stack.pop()!;
      if (x === id) {
        problem(e.file, "its superseded_by links lead back to it", "IDENTIFIERS-7");
        break;
      }
      if (seen.has(x) || !entries.has(x)) continue;
      seen.add(x);
      stack.push(...asList(entries.get(x)!.meta.superseded_by));
    }
  }

  // An entry that relates to a split rule or format relates to every entry of the split, and every
  // build it lists is listed by one of them.
  for (const [id, e] of entries) {
    if (KINDS[e.kind].statuses !== "claim" || e.meta.status === "superseded") continue;
    const related = asList(e.meta.related);
    for (const x of related) {
      const other = entries.get(x);
      // A superseded part cannot be related to, so it is left out of the group.
      const group = other ? [x, ...asList(other.meta.split_with).filter((g) => !isSuperseded(entries, g))] : [];
      if (group.length < 2 || group.includes(id)) continue;
      for (const g of group)
        if (!related.includes(g))
          problem(e.file, `relates to ${x}, which is split with ${g}; add ${g} to related`, "ENTRY-TYPES-6");
      for (const b of asList(e.meta.builds))
        if (!group.some((g) => asList(entries.get(g)?.meta.builds).includes(b)))
          problem(e.file, `lists ${b}, which no entry of the split ${group.join(", ")} lists`, "ENTRY-TYPES-8");
    }
  }
}
