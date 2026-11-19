import { Column, Entity, OneToMany } from 'typeorm';

@Entity('cats')
export class Cat {
  @Column()
  name: string;

  @OneToMany(() => Cat, (c) => c.name)
  kittens: Cat[];
}
