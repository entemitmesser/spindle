import { ImageOff } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

interface PosterImageProps {
  src: string | null | undefined;
  alt: string;
  className?: string;
}

/** TMDB poster with a graceful fallback (ui-design.md's Part B gotcha: broken
 * posters in a grid look worse than no posters). */
export function PosterImage({ src, alt, className }: PosterImageProps) {
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    return (
      <div
        className={cn("bg-muted text-muted-foreground flex items-center justify-center", className)}
        role="img"
        aria-label={alt}
      >
        <ImageOff className="size-5" aria-hidden="true" />
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn("object-cover", className)}
    />
  );
}
