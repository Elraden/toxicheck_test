import { createRouter, createWebHistory } from "vue-router";
import SplashView from "./views/SplashView.vue";
import WelcomeView from "./views/WelcomeView.vue";
import HomeView from "./views/HomeView.vue";
import AppLayout from "./layouts/AppLayout.vue";
import HistoryScreen from "./views/HistoryScreen.vue";
import KnowledgeView from "./views/KnowledgeView.vue";
import IngredientView from "./views/IngredientView.vue";
import PreferencesScreen from "./views/PreferencesScreen.vue";
import ScannerView from "./views/ScannerView.vue";
import ProductResultView from "./views/ProductResultView.vue";

export const router = createRouter({
  routes: [
  {
    path: "/",
    name: "splash",
    component: SplashView
  },
  {
     path: "/welcome",
     name: "welcome",
     component: WelcomeView
  },
  {
    path: "/home",
    redirect: { name: "home" }
  },
  {
    path: "/history",
    redirect: { name: "history" }
  },
  {
    path: "/scan",
    redirect: { name: "scan" }
  },
  {
    path: "/result/:barcode?",
    redirect: (to) => ({
      name: "scan-result",
      params: to.params
    })
  },
  {
    path: "/compare",
    redirect: { name: "knowledge" }
  },
  {
    path: "/knowledge",
    redirect: { name: "knowledge" }
  },
  {
    path: "/preferences",
    redirect: { name: "preferences" }
  },
  {
    path: "/app",
    component: AppLayout,
    children: [
      {
      path: '',
      redirect: { name: "home"}
      },
      {
        path: "home",
        name: "home",
        component: HomeView
      },
      {
        path: "history",
        name: "history",
        component: HistoryScreen
      },
      {
        path: "scan",
        name: "scan",
        component: ScannerView
      },
      {
        path: "result/:barcode?",
        name: "scan-result",
        component: ProductResultView
      },
      {
        path: "compare",
        name: "compare",
        redirect: { name: "knowledge" }
      },
      {
        path: "knowledge",
        name: "knowledge",
        component: KnowledgeView
      },
      {
        path: "knowledge/:id",
        name: "ingredient",
        component: IngredientView
      },
      {
        path: "preferences",
        name: "preferences",
        component: PreferencesScreen
      },
    ]
  }

],
  history: createWebHistory(),
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition || { top: 0 };
  }
});
