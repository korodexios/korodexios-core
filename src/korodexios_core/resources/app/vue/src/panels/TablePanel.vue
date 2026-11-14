<script setup lang="ts">
import { computed } from "vue";

import { dig } from "../kdx";
import type { ColumnSpec, PanelSpec } from "../kdx";
import { show, useSource } from "../source";
import { t } from "../state";
import MediaCell from "./MediaCell.vue";

const props = defineProps<{ module: string; panel: PanelSpec }>();
const { data, error } = useSource(() => props.panel);
const rows = computed(() => (Array.isArray(data.value) ? (data.value as unknown[]) : []));
const columns = computed<ColumnSpec[]>(() => {
  if (props.panel.columns?.length) return props.panel.columns;
  const first = rows.value[0];
  return first && typeof first === "object"
    ? Object.keys(first as object).map((key) => ({ key, label: key }))
    : [{ key: "", label: "value" }];
});
const cell = (row: unknown, column: ColumnSpec) => (column.key ? dig(row, column.key) : row);
</script>

<template>
  <p v-if="error" class="error">{{ error }}</p>
  <table v-else>
    <thead>
      <tr>
        <th v-for="column in columns" :key="column.key">{{ column.label }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, index) in rows" :key="index">
        <td v-for="column in columns" :key="column.key">
          <MediaCell v-if="column.kind === 'media'" :value="cell(row, column)" />
          <template v-else>{{ show(cell(row, column)) }}</template>
        </td>
      </tr>
      <tr v-if="!rows.length">
        <td :colspan="columns.length" class="muted">{{ t("Nothing to show.") }}</td>
      </tr>
    </tbody>
  </table>
</template>
