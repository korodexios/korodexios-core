// A "media" cell holds a path under a static mount of this daemon: audio, video, an image, a
// PDF. With the session cookie (or an open mount) the element's src is the path itself and the
// browser streams it with Range requests; with a header token the file is fetched once and
// played from a blob, because an <audio src> cannot send a header.
import { ApiError, request } from "./api";
import { hasToken } from "./auth";

export type MediaKind = "audio" | "video" | "image" | "pdf" | "file";

const KINDS: Record<string, MediaKind> = {
  mp3: "audio", wav: "audio", ogg: "audio", oga: "audio", flac: "audio", m4a: "audio", aac: "audio", opus: "audio",
  mp4: "video", webm: "video", ogv: "video", mov: "video",
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", avif: "image", svg: "image",
  pdf: "pdf",
};

/** The path when it is a same-host path outside /api/, else "" (never another host). */
export function mediaPath(value: unknown): string {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text.startsWith("/") || text.startsWith("//") || text.includes("..")) return "";
  if (text.startsWith("/api/")) return "";
  return text;
}

export function mediaKind(path: string): MediaKind {
  const clean = path.split(/[?#]/)[0] ?? "";
  const extension = clean.slice(clean.lastIndexOf(".") + 1).toLowerCase();
  return KINDS[extension] ?? "file";
}

export interface MediaSource {
  url: string;
  release: () => void;
}

/** A URL an element can load: the path itself, or a blob URL fetched with the token header. */
export async function mediaSource(path: string, signal?: AbortSignal): Promise<MediaSource> {
  if (!hasToken()) return { url: path, release: () => undefined };
  const response = await request(path, { signal });
  if (!response.ok) throw new ApiError(response.status, `HTTP ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  return { url, release: () => URL.revokeObjectURL(url) };
}
