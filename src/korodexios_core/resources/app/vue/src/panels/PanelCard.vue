<script setup lang="ts">
import { computed } from "vue";

import type { PanelSpec } from "../kdx";
import { overrideFor } from "../overrides";
import { GENERIC } from "./generic";

const props = defineProps<{ module: string; panel: PanelSpec }>();
const component = computed(() => overrideFor(props.module, props.panel.id) ?? GENERIC[props.panel.type]);
</script>

<template>
  <section class="card panel" :data-panel="`${module}.${panel.id}`">
    <h2>{{ panel.title }}</h2>
    <p v-if="panel.help" class="muted">{{ panel.help }}</p>
    <component :is="component" :module="module" :panel="panel" />
  </section>
</template>
