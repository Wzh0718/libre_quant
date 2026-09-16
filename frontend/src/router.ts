import { createRouter, createWebHistory } from "vue-router";
import BattlePage from "./pages/BattlePage.vue";
import ReviewPage from "./pages/ReviewPage.vue";
import AccountsPage from "./pages/AccountsPage.vue";
import StrategiesPage from "./pages/StrategiesPage.vue";
import TodayPage from "./pages/TodayPage.vue";
import AnalysisPage from "./pages/AnalysisPage.vue";
import ReplayPage from "./pages/ReplayPage.vue";
import ShadowPage from "./pages/ShadowPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    // ---- 作战闭环四页（主导航）
    { path: "/", redirect: "/battle" },
    { path: "/battle", name: "battle", component: BattlePage,
      meta: { title: "作战台" } },
    { path: "/review", name: "review", component: ReviewPage,
      meta: { title: "复盘" } },
    { path: "/accounts", name: "accounts", component: AccountsPage,
      meta: { title: "我的盘" } },
    { path: "/strategies", name: "strategies", component: StrategiesPage,
      meta: { title: "策略库" } },
    // ---- 旧研究页（不进主导航，保留路由避免旧链接 404）
    { path: "/workbench", redirect: "/review" },
    { path: "/today", name: "today", component: TodayPage,
      meta: { title: "今日决策" } },
    { path: "/analysis", name: "analysis", component: AnalysisPage,
      meta: { title: "深度分析" } },
    { path: "/replay", name: "replay", component: ReplayPage,
      meta: { title: "复盘模拟" } },
    { path: "/shadow", name: "shadow", component: ShadowPage,
      meta: { title: "影子盘" } },
  ],
});
