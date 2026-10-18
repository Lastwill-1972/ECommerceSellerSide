<script setup lang="ts">
import type { Page } from '@/shared/types/api'
defineProps<{ page: Page<unknown> | null; busy?: boolean }>()
defineEmits<{ change: [offset: number] }>()
</script>
<template><div v-if="page" class="pager"><span>共 {{ page.total }} 条 · 第 {{ Math.floor(page.offset / page.limit) + 1 }} 页</span><div><el-button size="small" :disabled="busy || page.offset === 0" @click="$emit('change', Math.max(0, page.offset - page.limit))">上一页</el-button><el-button size="small" :disabled="busy || page.offset + page.limit >= page.total || page.offset + page.limit > 100000" @click="$emit('change', page.offset + page.limit)">下一页</el-button></div></div></template>
