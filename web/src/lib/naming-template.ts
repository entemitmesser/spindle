/**
 * Client-side mirror of `utils/naming.py`'s restricted formatter
 * (architecture.md §6) — gives the Library settings tab inline validation
 * and a live preview without a round trip. The server is still the source
 * of truth (`PUT /settings` re-validates and 422s on the same rules).
 */
export const NAMING_VARIABLES = [
  "title",
  "original_title",
  "year",
  "season",
  "episode",
  "episode_title",
  "tmdb_id",
  "imdb_id",
  "source",
  "resolution",
  "video_codec",
  "audio_codec",
  "edition",
] as const;

const SAMPLE_VALUES: Record<string, string | number> = {
  title: "Blade Runner 2049",
  original_title: "Blade Runner 2049",
  year: 2017,
  season: 1,
  episode: 1,
  episode_title: "Pilot",
  tmdb_id: 335984,
  imdb_id: "tt1856101",
  source: "Bluray",
  resolution: "2160p",
  video_codec: "hevc",
  audio_codec: "dts",
  edition: "Director's Cut",
};

function tokenName(raw: string): string {
  const cut = raw.search(/[:!]/);
  return cut === -1 ? raw : raw.slice(0, cut);
}

/** Returns an error message, or `null` if the template is valid. */
export function validateTemplate(template: string): string | null {
  const tokenRe = /\{([^{}]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = tokenRe.exec(template)) !== null) {
    const raw = match[1];
    if (raw === "") return "Empty placeholder: {}";
    const name = tokenName(raw);
    if (/[.[]/.test(name)) return `Attribute/index access isn't allowed: {${raw}}`;
    if (!(NAMING_VARIABLES as readonly string[]).includes(name)) {
      return `Unknown variable: {${name}}`;
    }
  }
  return null;
}

function formatNumber(value: number, spec: string): string | null {
  const match = /^0(\d+)d$/.exec(spec);
  if (!match) return null;
  return String(value).padStart(Number(match[1]), "0");
}

export function renderTemplatePreview(template: string): string {
  if (validateTemplate(template) !== null) return template;
  return template.replace(/\{([^{}]*)\}/g, (whole, raw: string) => {
    const colonIndex = raw.indexOf(":");
    const name = colonIndex === -1 ? raw : raw.slice(0, colonIndex);
    const spec = colonIndex === -1 ? "" : raw.slice(colonIndex + 1);
    const value = SAMPLE_VALUES[name];
    if (value === undefined) return whole;
    if (spec && typeof value === "number") {
      const formatted = formatNumber(value, spec);
      if (formatted !== null) return formatted;
    }
    return String(value);
  });
}
