<script setup lang="ts">
import { computed } from "vue";

import type { PanelSpec } from "../kdx";
import { t } from "../state";

const props = defineProps<{ module: string; panel: PanelSpec }>();
const target = computed(() =>
  props.panel.module ? `--module ${props.panel.module}` : props.panel.group ? `--group ${props.panel.group}` : "",
);
</script>

<template>
  <p class="muted">
    {{ t("Settings are edited in the built-in console or in the terminal:") }}
    <code>kdx config schema {{ target }}</code>
  </p>
</template>
