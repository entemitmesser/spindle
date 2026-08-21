import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { PosterImage } from "@/components/common/PosterImage";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  ApiError,
  type MediaKind,
  type MetadataCandidate,
  type MetadataSearchResult,
  api,
} from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";

interface IdentifyDialogProps {
  jobId: number | null;
  onOpenChange: (open: boolean) => void;
}

interface Match {
  tmdbId: number;
  title: string;
  year: number | null;
  posterUrl: string | null;
}

function CandidateRow({
  candidate,
  onSelect,
  interactive = true,
}: {
  candidate: MetadataCandidate | MetadataSearchResult;
  onSelect?: () => void;
  /** false when an ancestor (a `CommandItem`) already provides the clickable
   * semantics — avoids nesting a real button inside it. */
  interactive?: boolean;
}) {
  const confidence = "confidence" in candidate ? candidate.confidence : null;
  const content = (
    <>
      <PosterImage
        src={candidate.poster_url}
        alt={candidate.title}
        className="h-16 w-11 shrink-0 rounded-sm"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{candidate.title}</p>
        <p className="text-muted-foreground text-xs">{candidate.year ?? "Unknown year"}</p>
      </div>
      {confidence !== null && (
        <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
          {Math.round(confidence * 100)}% match
        </span>
      )}
    </>
  );

  if (!interactive) {
    return <div className="flex w-full items-center gap-3 p-2 text-left">{content}</div>;
  }

  return (
    <button
      type="button"
      onClick={onSelect}
      className="hover:bg-muted flex w-full items-center gap-3 rounded-md p-2 text-left transition-colors"
    >
      {content}
    </button>
  );
}

export function IdentifyDialog({ jobId, onOpenChange }: IdentifyDialogProps) {
  const queryClient = useQueryClient();
  const open = jobId !== null;

  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [mediaKind, setMediaKind] = useState<MediaKind>("movie");
  const [selected, setSelected] = useState<Match | null>(null);
  const [manualTitle, setManualTitle] = useState("");
  const [manualYear, setManualYear] = useState("");
  const [season, setSeason] = useState("");
  const [saving, setSaving] = useState(false);

  const { data: job } = useQuery({
    queryKey: queryKeys.jobs.detail(jobId ?? -1),
    queryFn: () => api.jobs.get(jobId!),
    enabled: open,
  });

  const { data: searchResults, isFetching: searching } = useQuery({
    queryKey: ["metadata", "search", debouncedQuery, mediaKind],
    queryFn: () => api.metadata.search(debouncedQuery, mediaKind),
    enabled: open && debouncedQuery.trim().length > 1,
  });

  // Adjust local state during render instead of in an effect (React's
  // "adjusting state when a prop changes" pattern) — `hydratedFor` tracks
  // which job the fields currently reflect, and each branch below only fires
  // once per transition since it flips the guard it checks.
  const [hydratedFor, setHydratedFor] = useState<number | null>(null);
  if (!open && hydratedFor !== null) {
    setHydratedFor(null);
    setQuery("");
    setSelected(null);
    setManualTitle("");
    setManualYear("");
    setSeason("");
    setMediaKind("movie");
  } else if (open && job && hydratedFor !== job.id) {
    setHydratedFor(job.id);
    setMediaKind(job.media_kind);
    setManualTitle(job.title ?? "");
    setManualYear(job.year ? String(job.year) : "");
  }

  async function apply(patch: {
    tmdb_id: number | null;
    title: string;
    year: number | null;
    media_kind: MediaKind;
    season: number | null;
  }) {
    if (jobId === null) return;
    setSaving(true);
    try {
      await api.jobs.update(jobId, patch);
      toast.success("Identification saved — job re-queued.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.detail(jobId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.queue.list() });
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Couldn't save identification.");
    } finally {
      setSaving(false);
    }
  }

  const seasonValue = season.trim() ? Number(season) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Identify disc</DialogTitle>
          <DialogDescription>
            Confirm what this disc is, or search TMDB / enter it manually.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Label htmlFor="identify-kind" className="shrink-0">
              Type
            </Label>
            <Select value={mediaKind} onValueChange={(v) => setMediaKind(v as MediaKind)}>
              <SelectTrigger id="identify-kind" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="movie">Movie</SelectItem>
                <SelectItem value="series">Series</SelectItem>
                <SelectItem value="music">Music</SelectItem>
                <SelectItem value="other">Other</SelectItem>
              </SelectContent>
            </Select>
            {mediaKind === "series" && (
              <>
                <Label htmlFor="identify-season" className="shrink-0">
                  Season
                </Label>
                <Input
                  id="identify-season"
                  type="number"
                  min={1}
                  className="w-20"
                  value={season}
                  onChange={(e) => setSeason(e.target.value)}
                />
              </>
            )}
          </div>

          {job && job.candidates.length > 0 && (
            <div className="space-y-1">
              <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
                Candidates
              </p>
              <div className="space-y-1">
                {job.candidates.map((candidate) => (
                  <CandidateRow
                    key={candidate.tmdb_id ?? candidate.title}
                    candidate={candidate}
                    onSelect={() =>
                      candidate.tmdb_id !== null &&
                      candidate.tmdb_id !== undefined &&
                      setSelected({
                        tmdbId: candidate.tmdb_id,
                        title: candidate.title,
                        year: candidate.year ?? null,
                        posterUrl: candidate.poster_url ?? null,
                      })
                    }
                  />
                ))}
              </div>
            </div>
          )}

          <Command shouldFilter={false} className="rounded-lg border">
            <CommandInput placeholder="Search TMDB…" value={query} onValueChange={setQuery} />
            <CommandList>
              {searching && <div className="text-muted-foreground p-3 text-sm">Searching…</div>}
              {!searching && debouncedQuery.trim().length > 1 && (
                <CommandEmpty>No results.</CommandEmpty>
              )}
              {searchResults && searchResults.items.length > 0 && (
                <CommandGroup heading="Search results">
                  {searchResults.items.map((result) => (
                    <CommandItem
                      key={result.tmdb_id}
                      value={String(result.tmdb_id)}
                      onSelect={() =>
                        setSelected({
                          tmdbId: result.tmdb_id,
                          title: result.title,
                          year: result.year,
                          posterUrl: result.poster_url,
                        })
                      }
                      className="p-0"
                    >
                      <CandidateRow candidate={result} interactive={false} />
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
            </CommandList>
          </Command>

          {selected && (
            <div className="bg-muted flex items-center gap-3 rounded-lg p-3">
              <PosterImage
                src={selected.posterUrl}
                alt={selected.title}
                className="h-16 w-11 shrink-0 rounded-sm"
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{selected.title}</p>
                <p className="text-muted-foreground text-xs">{selected.year ?? "Unknown year"}</p>
              </div>
              <Button
                disabled={saving}
                onClick={() =>
                  apply({
                    tmdb_id: selected.tmdbId,
                    title: selected.title,
                    year: selected.year,
                    media_kind: mediaKind,
                    season: seasonValue,
                  })
                }
              >
                Use this match
              </Button>
            </div>
          )}

          <div className="space-y-2 border-t pt-4">
            <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
              Manual entry
            </p>
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1">
                <Label htmlFor="manual-title">Title</Label>
                <Input
                  id="manual-title"
                  value={manualTitle}
                  onChange={(e) => setManualTitle(e.target.value)}
                  placeholder="TMDB doesn't know this one"
                />
              </div>
              <div className="w-24 space-y-1">
                <Label htmlFor="manual-year">Year</Label>
                <Input
                  id="manual-year"
                  type="number"
                  value={manualYear}
                  onChange={(e) => setManualYear(e.target.value)}
                />
              </div>
              <Button
                variant="outline"
                disabled={saving || !manualTitle.trim()}
                onClick={() =>
                  apply({
                    tmdb_id: null,
                    title: manualTitle.trim(),
                    year: manualYear.trim() ? Number(manualYear) : null,
                    media_kind: mediaKind,
                    season: seasonValue,
                  })
                }
              >
                Save
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
