import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';
import { applyTheme, initialTheme } from './app/services/theme.service';

applyTheme(initialTheme());

bootstrapApplication(App, appConfig)
  .catch((err) => console.error(err));
