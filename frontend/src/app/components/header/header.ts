import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import {
  mdiImageSearchOutline,
  mdiServerOutline,
  mdiTune,
  mdiWeatherNight,
  mdiWeatherSunny,
} from '@mdi/js';
import { StoreService } from '../../services/store.service';
import { ThemeService } from '../../services/theme.service';
import { IconComponent } from '../icon/icon';

@Component({
  selector: 'app-header',
  standalone: true,
  imports: [IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './header.html',
  styleUrl: './header.css',
})
export class HeaderComponent {
  readonly store = inject(StoreService);
  readonly theme = inject(ThemeService);
  readonly browseClick = output<void>();
  readonly settingsClick = output<void>();

  readonly mdiImageSearchOutline = mdiImageSearchOutline;
  readonly mdiServerOutline = mdiServerOutline;
  readonly mdiTune = mdiTune;
  readonly mdiWeatherNight = mdiWeatherNight;
  readonly mdiWeatherSunny = mdiWeatherSunny;
}
