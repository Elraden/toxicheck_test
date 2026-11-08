import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir:'./tests/browser', timeout:30000, workers:1,
  use:{
    baseURL:'http://127.0.0.1:5178', viewport:{width:390,height:844},
    permissions:['camera'],
    launchOptions:{channel:process.env.PLAYWRIGHT_CHANNEL||undefined,
      args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']},
  },
  webServer:{command:'npm run dev -- --host 127.0.0.1 --port 5178 --strictPort',
    url:'http://127.0.0.1:5178',reuseExistingServer:!process.env.CI,timeout:30000},
});
