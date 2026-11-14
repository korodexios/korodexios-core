<script setup lang="ts">
import type { PanelSpec } from "../kdx";

defineProps<{ module: string; panel: PanelSpec }>();
</script>

<template>
  <p>{{ panel.text }}</p>
</template>
