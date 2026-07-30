import { ChangeDetectionStrategy, Component, inject, output } from '@angular/core';
import { StoreService } from '../../services/store.service';

@Component({
  selector: 'app-header',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './header.html',
  styleUrl: './header.css',
})
export class HeaderComponent {
  readonly store = inject(StoreService);
  readonly browseClick = output<void>();
}
