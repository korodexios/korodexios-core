<script setup lang="ts">
import { computed } from "vue";

import type { PanelSpec } from "../kdx";
import { show, useSource } from "../source";

const props = defineProps<{ module: string; panel: PanelSpec }>();
const { data, error } = useSource(() => props.panel);
const text = computed(() =>
  Array.isArray(data.value) ? data.value.join("\n") : show(data.value === undefined ? "" : data.value),
);
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <pre v-else class="terminal">{{ text }}</pre>
</template>
