<script setup lang="ts">
import { computed } from "vue";

import type { PanelSpec } from "../kdx";
import { show, useSource } from "../source";

const props = defineProps<{ module: string; panel: PanelSpec }>();
const { data, error } = useSource(() => props.panel);
const pairs = computed<[string, unknown][]>(() =>
  data.value && typeof data.value === "object" ? Object.entries(data.value) : [["value", data.value]],
);
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <table v-else>
    <tbody>
      <tr v-for="[key, value] in pairs" :key="key">
        <th>{{ key }}</th>
        <td>{{ show(value) }}</td>
      </tr>
    </tbody>
  </table>
</template>
