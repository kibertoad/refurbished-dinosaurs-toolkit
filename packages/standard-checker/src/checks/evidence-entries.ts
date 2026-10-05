// The checks of findings and experiments: a finding's locations, and an experiment's fixture,
// starting state and recording.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Context } from "../context.ts";
import { isSuperseded } from "../evidence.ts";
import { asList } from "../ids.ts";
import { checkAddress, checkOffset } from "../locations.ts";
import { locationRule } from "../standard.ts";
import type { Entry, Yaml } from "../types.ts";
import { parseScalar } from "../yaml.ts";

/** Checks a finding's method, environment and locations. */
export function checkFinding(ctx: Context, e: Entry) {
  const { problem } = ctx;
  const { buildFiles, codeRanges } = ctx.spec;
  const { file, meta } = e;
  if (!["static", "dynamic"].includes(meta.method)) problem(file, "method must be static or dynamic");
  if (meta.method === "static" && meta.environment !== null) problem(file, "a static finding has environment: null");
  if (meta.method === "dynamic" && (meta.environment === null || meta.environment === ""))
    problem(file, "a dynamic finding gives its environment");
  const locations = asList(meta.locations);
  const builds = asList(meta.builds);
  if (meta.method === "static")
    for (const b of builds)
      if (!locations.some((l) => l && l.build === b))
        problem(file, `a static finding has at least one location in ${b}`);
  for (const loc of locations) {
    if (!loc || typeof loc !== "object") {
      problem(file, "a location must be a map of build, file and address or offset");
      continue;
    }
    if (!builds.includes(loc.build)) problem(file, `location build ${loc.build} is not in builds`);
    const files = buildFiles.get(loc.build) ?? [];
    const bf = files.find((f) => f.path === loc.file);
    if (!bf) {
      problem(file, `location file ${loc.file} is not in the files of ${loc.build}`);
      continue;
    }
    const format = bf.unpacked?.format ?? bf.format;
    // kind tells code from data within an executable; a data file holds no code to tell apart.
    if (loc.kind !== undefined && !locationRule(format)?.address)
      problem(
        file,
        `location kind ${loc.kind} in ${loc.file}: a ${format} file is not an executable, so its locations give no kind`,
      );
    else if (loc.kind !== undefined && loc.kind !== "code" && loc.kind !== "file-data")
      problem(file, `location kind ${loc.kind} in ${loc.file}: kind must be code or file-data when given`);
    const fileData = loc.kind === "file-data";
    if (fileData && "address" in loc)
      problem(file, `a file-data location in ${loc.file} gives a file offset, not an address`);
    // A file-data location in a packed file may name bytes that exist only once it is unpacked,
    // such as the relocation table an unpacker writes, by an offset into the unpacked form.
    const intoUnpacked = loc.unpacked === true;
    if ("unpacked" in loc && !intoUnpacked)
      problem(file, `a location in ${loc.file} gives unpacked: true or leaves it out`);
    else if (intoUnpacked && !fileData)
      problem(file, `a location in ${loc.file} gives unpacked: true only with kind: file-data`);
    else if (intoUnpacked && !bf.packer)
      problem(file, `a location in ${loc.file} gives unpacked: true, but ${loc.file} is not packed`);
    if ("address" in loc && "offset" in loc) problem(file, "a location gives address or offset, not both");
    if ("address" in loc) {
      if (!fileData) checkAddress(problem, file, loc.address, format);
    } else if ("offset" in loc) {
      // Explicit file-data offsets name shipped container metadata or data, never code.
      // Other offsets name bytes of the shipped file: data, CD audio, or MZ overlay code
      // outside the load image. The finding must establish the overlay mapping. Like an
      // address, an offset is judged by the unpacked format, so a packed MZ stub around LE
      // or PE code cannot use offsets for code the loader maps.
      const rule = locationRule(format);
      if (!fileData && rule && !rule.offset)
        problem(
          file,
          `location in ${loc.file} gives an offset; a ${format} executable is located by address (only MZ overlay code uses offsets)`,
        );
      const range =
        intoUnpacked && bf.packer
          ? checkOffset(
              problem,
              file,
              loc.offset,
              { path: bf.path, size: Number(bf.unpacked?.size) },
              "unpacked form of",
            )
          : checkOffset(problem, file, loc.offset, bf);
      // An offset into an executable locates overlay code, so it lies wholly inside one row of
      // the build's Code ranges. Adjacent rows are not joined: a range that crosses from one
      // into the next, such as into another bank, fails.
      if (!fileData && range && rule?.offset && rule.address && codeRanges.has(loc.build)) {
        const inside = codeRanges
          .get(loc.build)!
          .some((r) => r.file === loc.file && r.start <= range[0] && range[1] <= r.end);
        if (!inside)
          problem(
            file,
            `offset ${loc.offset} in ${loc.file} does not lie wholly inside one of the rows the Code ranges section of ${loc.build} gives for that file`,
          );
      }
    } else problem(file, "a location gives an address or an offset");
  }
}

// A run's draws from the generator, in order. Each is named by the rule it was made under, which
// the rebuild cites too, and never by the address of the call in the original's code. A live
// experiment's draws cite living rules, as its other links do.
const DRAW_KEYS = ["rule", "bound", "result"];
function checkDraws(ctx: Context, fixture: string, draws: Yaml, live: boolean) {
  const { problem } = ctx;
  const { entries } = ctx.spec;
  if (draws === undefined) return;
  if (!Array.isArray(draws)) return problem(fixture, "draws is a list");
  draws.forEach((draw: Yaml, i: number) => {
    if (draw === null || typeof draw !== "object" || Array.isArray(draw))
      return problem(fixture, `draw ${i} is an object with rule, bound and result`);
    const extra = Object.keys(draw).filter((k) => !DRAW_KEYS.includes(k));
    if (extra.length) problem(fixture, `draw ${i} has ${extra.join(", ")}; a draw gives only rule, bound and result`);
    if (entries.get(draw.rule)?.kind !== "RULE")
      problem(fixture, `draw ${i} names ${draw.rule}, which is not a rule entry`);
    else if (live && isSuperseded(entries, draw.rule))
      problem(fixture, `draw ${i} names ${draw.rule}, which is superseded`, "STATUS-17");
    if (!Number.isInteger(draw.bound) || !Number.isInteger(draw.result))
      problem(fixture, `draw ${i} gives bound and result as integers`);
  });
}

/** Checks an experiment's builds, fixture, starting state and recording. live is false once it is superseded. */
export function checkExperiment(ctx: Context, id: string, e: Entry, isSup: boolean) {
  const { problem } = ctx;
  const { buildFiles, glossary } = ctx.spec;
  const { specDir } = ctx.config;
  const { file, meta } = e;
  const builds = asList(meta.builds);
  if (builds.length !== 1) problem(file, "an experiment lists exactly one build", "ENTRY-TYPES-7");
  // The standard's experiment entry lists the starting states: a save patch, a save, new-game,
  // emulated-call or null, where a save or a patch is in saves/. A missing field is reported with
  // the other required fields. Which save hashes the fixture needs depends on the form, so an
  // unknown one gets no save hash problem. A path in saves/ names a file under it, so it has a
  // name after saves/ and no empty, . or .. segment.
  const state = meta.starting_state;
  const savePath =
    typeof state === "string" &&
    state.startsWith("saves/") &&
    state
      .split("/")
      .slice(1)
      .every((s) => s !== "" && s !== "." && s !== "..");
  const fromSave = state === null || savePath;
  if (state !== undefined && !fromSave && state !== "new-game" && state !== "emulated-call") {
    // A string the front matter would read as another value, such as a quoted "null", is shown quoted.
    const shown =
      typeof state === "string" && state !== "" && parseScalar(state) === state ? state : JSON.stringify(state);
    problem(
      file,
      `starting_state ${shown} is none of the forms the standard defines: a save or save patch in saves/, new-game, emulated-call or null`,
    );
  }
  const fixture = meta.fixture && join(specDir, "experiments", meta.fixture);
  if (!fixture || !existsSync(fixture)) problem(file, `fixture ${meta.fixture} does not exist`);
  else {
    try {
      const fx = JSON.parse(readFileSync(fixture, "utf8"));
      if (fx.experiment !== id) problem(fixture, `experiment must be ${id}`);
      // Only a new game and an emulated call start without a save. starting_state null still has
      // one: a save that cannot be committed, kept with the captures and found by its hash.
      const save = fx.starting_state;
      if (fromSave && !save?.xxh3) {
        const where = "gives the hash of the save its runs started from in starting_state.xxh3";
        problem(
          fixture,
          state === null
            ? `${where}; starting_state null names a save kept with the captures, and an experiment that starts without a save has starting_state new-game or emulated-call`
            : where,
        );
      }
      if (savePath && state.endsWith(".patch.json") && !save?.base_xxh3)
        problem(fixture, "a patch fixture gives the base save's hash as well");
      for (const key of ["xxh3", "base_xxh3"])
        if (save?.[key] && !/^[0-9a-f]{32}$/.test(String(save[key])))
          problem(fixture, `starting_state.${key} must be 32 lower-case hex digits`);
      for (const run of asList(fx.runs)) {
        for (const ev of asList(run?.events))
          if (!glossary.has(ev?.event)) problem(fixture, `event ${ev?.event} has no glossary entry`);
        checkDraws(ctx, fixture, run?.draws, !isSup);
      }
      if (typeof meta.recording === "string" && meta.recording !== "" && !fx.recording_xxh3)
        problem(fixture, "an experiment with a recording gives the recording's hash in recording_xxh3");
    } catch (err) {
      problem(fixture, `is not valid JSON: ${(err as Error).message}`);
    }
  }
  if (savePath && !existsSync(join(specDir, "experiments", state)))
    problem(file, `starting_state ${state} does not exist`);
  // A recording is committed in recordings/, kept with the captures, or one of the build's files.
  if (typeof meta.recording === "string" && meta.recording !== "") {
    const rec = meta.recording;
    if (rec.startsWith("recordings/")) {
      if (!existsSync(join(specDir, "experiments", rec))) problem(file, `recording ${rec} does not exist`);
    } else if (
      !rec.startsWith("captures/") &&
      !builds.some((b) => (buildFiles.get(b) ?? []).some((f) => f.path === rec))
    )
      problem(file, `recording ${rec} is neither in recordings/ or captures/ nor a file of ${builds.join(", ")}`);
  }
}
