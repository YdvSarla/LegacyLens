<?php

namespace Tests\Feature;

use App\Models\User;
use PHPUnit\Framework\TestCase;

class UserTest extends TestCase
{
    public function testUserModelExists(): void
    {
        $this->assertTrue(class_exists(User::class));
    }
}
