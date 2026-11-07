import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * Renders a Material Design Icon from @mdi/js, the icon set Immich uses.
 * Usage: `<app-icon [path]="mdiClose" [size]="20" />` with
 * `readonly mdiClose = mdiClose;` imported from '@mdi/js' in the component.
 */
@Component({
  selector: 'app-icon',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './icon.html',
  styleUrl: './icon.css',
})
export class IconComponent {
  readonly path = input.required<string>();
  readonly size = input<number | string>(20);
  readonly label = input<string>('');
}
