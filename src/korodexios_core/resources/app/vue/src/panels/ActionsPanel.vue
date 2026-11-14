<script setup lang="ts">
import type { PanelSpec } from "../kdx";
import ActionButton from "./ActionButton.vue";

defineProps<{ module: string; panel: PanelSpec }>();
</script>

<template>
  <div class="row">
    <ActionButton v-for="button in panel.buttons ?? []" :key="button.id" :button="button" />
  </div>
</template>
