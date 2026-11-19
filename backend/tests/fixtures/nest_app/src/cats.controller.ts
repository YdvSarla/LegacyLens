import { Controller, Get, Param, Post } from '@nestjs/common';
import { CatsService } from './cats.service';

@Controller('cats')
export class CatsController {
  constructor(private readonly cats: CatsService) {}

  @Get(':id')
  findOne(@Param('id') id: string) {
    return this.cats.find(id);
  }

  @Post()
  create() {
    return this.cats.add();
  }
}
