<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";

import { mediaKind, mediaPath, mediaSource } from "../kdx";
import { show } from "../source";

// A media cell: plays or shows a file of a static mount of this daemon (src/kdx/media.ts).
const props = defineProps<{ value: unknown }>();
const path = computed(() => mediaPath(props.value));
const kind = computed(() => mediaKind(path.value));
const url = ref("");
const error = ref("");

watchEffect((onCleanup) => {
  const current = path.value;
  url.value = "";
  if (!current) return;
  const controller = new AbortController();
  let release = (): void => undefined;
  mediaSource(current, controller.signal)
    .then((source) => {
      release = source.release;
      url.value = source.url;
    })
    .catch((reason: Error) => (error.value = reason.message));
  onCleanup(() => {
    controller.abort();
    release();
  });
});
</script>

<template>
  <template v-if="!path">{{ show(value) }}</template>
  <span v-else-if="error" class="error">{{ error }}</span>
  <span v-else-if="!url" class="muted">…</span>
  <audio v-else-if="kind === 'audio'" controls preload="none" :src="url" />
  <video v-else-if="kind === 'video'" class="media" controls preload="none" :src="url" />
  <img v-else-if="kind === 'image'" class="media" :src="url" :alt="path" />
  <a v-else :href="url" target="_blank" rel="noopener noreferrer">{{ path.split("/").pop() }}</a>
</template>
