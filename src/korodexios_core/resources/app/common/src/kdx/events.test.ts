import { expect, test } from "vitest";

import { matches, readFrames } from "./events";
import type { BusEvent } from "./events";
import { mediaKind, mediaPath } from "./media";

function stream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
}

test("frames split across chunks are joined; comments and bad frames are skipped", async () => {
  const seen: BusEvent[] = [];
  const frame = (name: string) => `data: ${JSON.stringify({ name, source: "x", at: 1, sequence: 1, payload: {} })}\n\n`;
  const text = `retry: 3000\n: connected\n\n${frame("queue.done")}data: {broken\n\n${frame("player.track")}`;
  await readFrames(stream([text.slice(0, 20), text.slice(20, 61), text.slice(61)]), (event) => seen.push(event));
  expect(seen.map((event) => event.name)).toEqual(["queue.done", "player.track"]);
});

test("refresh_on prefixes match the start of an event name", () => {
  expect(matches("queue.task.done", ["queue."])).toBe(true);
  expect(matches("queue_other", ["queue."])).toBe(false);
  expect(matches("anything", ["*"])).toBe(true);
  expect(matches("anything", [])).toBe(false);
});

test("media cells accept only paths of this host outside /api/", () => {
  expect(mediaPath("/media/take-1.mp3")).toBe("/media/take-1.mp3");
  for (const bad of ["https://evil.test/a.mp3", "//evil.test/a.mp3", "/api/v1/x", "/a/../b", 3, null]) {
    expect(mediaPath(bad)).toBe("");
  }
  expect(mediaKind("/m/a.MP3?v=1")).toBe("audio");
  expect(mediaKind("/m/cover.webp")).toBe("image");
  expect(mediaKind("/m/score.pdf")).toBe("pdf");
  expect(mediaKind("/m/clip.webm")).toBe("video");
  expect(mediaKind("/m/data.bin")).toBe("file");
});
