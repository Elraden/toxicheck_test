import { createRouter, createWebHistory } from "vue-router";
import SplashView from "./views/SplashView.vue";
import WelcomeView from "./views/WelcomeView.vue";
import HomeView from "./views/HomeView.vue";
import AppLayout from "./layouts/AppLayout.vue";
import HistoryScreen from "./views/HistoryScreen.vue";
import CompareScreen from "./views/CompareScreen.vue";
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
    redirect: { name: "compare" }
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
        component: CompareScreen
      },
      {
        path: "preferences",
        name: "preferences",
        component: PreferencesScreen
      },
    ]
  }

],
  history: createWebHistory()
});
