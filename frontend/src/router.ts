import { createRouter, createWebHistory } from "vue-router";
import TodayPage from "./pages/TodayPage.vue";
import AnalysisPage from "./pages/AnalysisPage.vue";
import ReplayPage from "./pages/ReplayPage.vue";
import ReviewPage from "./pages/ReviewPage.vue";
import ShadowPage from "./pages/ShadowPage.vue";
import AccountsPage from "./pages/AccountsPage.vue";
import WorkbenchPage from "./pages/WorkbenchPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/workbench" },
    { path: "/workbench", name: "workbench", component: WorkbenchPage,
      meta: { title: "策略台" } },
    { path: "/today", name: "today", component: TodayPage,
      meta: { title: "今日决策" } },
    { path: "/analysis", name: "analysis", component: AnalysisPage,
      meta: { title: "深度分析" } },
    { path: "/replay", name: "replay", component: ReplayPage,
      meta: { title: "复盘模拟" } },
    { path: "/review", name: "review", component: ReviewPage,
      meta: { title: "历史复盘" } },
    { path: "/shadow", name: "shadow", component: ShadowPage,
      meta: { title: "影子盘" } },
    { path: "/accounts", name: "accounts", component: AccountsPage,
      meta: { title: "我的盘" } },
  ],
});
