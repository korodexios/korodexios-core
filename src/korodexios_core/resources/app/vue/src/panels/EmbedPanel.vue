<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import { serveEmbed } from "../kdx";
import type { PanelSpec } from "../kdx";

// A page of a static mount, framed; it gets this app's session by the embed handshake (v1).
defineProps<{ module: string; panel: PanelSpec }>();
const frame = ref<HTMLIFrameElement | null>(null);
let stop = (): void => undefined;

onMounted(() => {
  if (frame.value) stop = serveEmbed(frame.value);
});
onBeforeUnmount(() => stop());
</script>

<template>
  <iframe
    ref="frame"
    class="embed"
    :src="panel.src"
    :title="panel.title || panel.id"
    sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
    referrerpolicy="no-referrer"
    :style="{ height: `${panel.height ?? 360}px` }"
  />
</template>
